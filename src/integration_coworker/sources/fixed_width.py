"""
Fixed-width file source handler with confidence-gated detection.

Implements the SpecSource protocol for fixed-width files with:
- Rich confidence scoring (no silent heuristics)
- Explicit warnings propagated to ParsedSpec
- Multi-layout RecordLayout support
- Production-safe detection that won't steal CSV/TSV/delimited inputs

Detection uses weighted signals:
- Line length consistency
- Delimiter absence
- Boundary stability
- Extension hints

All thresholds centralized in fixed_width_config.py
"""

import logging
from typing import Dict, List, Optional, Tuple, Union

from .base import (
    ContentType,
    ParsedSpec,
    SourceType,
    extract_name_from_uri,
    normalize_content,
)

logger = logging.getLogger(__name__)


class FixedWidthSource:
    """
    Handler for fixed-width files with confidence-gated detection.
    
    Detection: Uses detect_with_confidence() which returns explicit
    scores and reasons. Only matches if confidence >= DETECT_MIN.
    
    Parsing: Uses infer_schema() which returns confidence and warnings.
    Warnings propagate to ParsedSpec for downstream visibility.
    
    Outputs FileSpec + FileField + RecordLayout for the Silver File Model.
    """
    
    def detect(
        self,
        content: ContentType,
        uri: str,
        content_type: str,
    ) -> float:
        """
        Detect if content is fixed-width format.
        
        Returns confidence score 0-1.
        
        Internally uses detect_with_confidence() with weighted signals:
        - Line consistency (35%)
        - Delimiter absence (25%)
        - Boundary stability (25%)
        - Extension hint (15%)
        
        Hard rejects (returns 0.0):
        - Consistent delimiter patterns (CSV/TSV/pipe)
        - Too few lines for analysis
        - Line lengths too variable
        """
        # Early reject for clearly non-fixed-width extensions
        uri_lower = uri.lower()
        if any(uri_lower.endswith(ext) for ext in ['.csv', '.tsv', '.json', '.xml', '.yaml', '.yml']):
            return 0.0
        
        content_str = normalize_content(content)
        
        try:
            from integration_coworker.parsers.fixed_width_parser import (
                detect_with_confidence,
            )
            from integration_coworker.parsers.fixed_width_config import get_config
            
            config = get_config()
            result = detect_with_confidence(content_str, uri, config)
            
            logger.debug(
                f"FixedWidthSource detection for {uri}: "
                f"matched={result.matched}, confidence={result.confidence:.2f}, "
                f"reasons={result.reasons}"
            )
            
            # Return raw confidence (router will compare against threshold)
            return result.confidence
            
        except ImportError as e:
            logger.warning(f"Fixed-width parser not available: {e}")
            return 0.0
        except Exception as e:
            logger.warning(f"Fixed-width detection error: {e}")
            return 0.0
    
    def parse(
        self,
        content: ContentType,
        uri: str,
    ) -> ParsedSpec:
        """
        Parse fixed-width content with explicit confidence and warnings.
        
        Returns ParsedSpec with:
        - source_type=FILE
        - data: {file_spec, fields, record_layouts}
        - warnings: Propagated from inference
        - confidence: From inference
        - metadata: Detection/inference breakdown for debugging
        
        If inference confidence < INFER_MIN:
        - Returns invalid ParsedSpec with errors
        - Does NOT proceed to persistence
        
        If inference confidence < INFER_WARN:
        - Returns valid ParsedSpec with warnings
        - Proceeds to persistence but flags issues
        """
        content_str = normalize_content(content)
        
        try:
            from integration_coworker.parsers.fixed_width_parser import (
                detect_with_confidence,
                infer_schema,
            )
            from integration_coworker.parsers.fixed_width_config import get_config
        except ImportError as e:
            return ParsedSpec(
                source_type=SourceType.FILE,
                source_uri=uri,
                data=None,
                errors=[f"Fixed-width parser not available: {e}"],
            )
        
        config = get_config()
        
        try:
            # Step 1: Run detection for metadata (already passed threshold to get here)
            detection_result = detect_with_confidence(content_str, uri, config)
            
            # Step 2: Run inference with confidence scoring
            inference_result = infer_schema(content_str, config=config)
            
            # Step 3: Check for hard failure
            if not inference_result.is_valid():
                return ParsedSpec(
                    source_type=SourceType.FILE,
                    source_uri=uri,
                    data=None,
                    errors=inference_result.errors,
                    warnings=inference_result.warnings,
                    confidence=inference_result.confidence,
                    metadata={
                        "detection": {
                            "matched": detection_result.matched,
                            "confidence": detection_result.confidence,
                            "signals": detection_result.signals,
                            "reasons": detection_result.reasons,
                        },
                        "inference": {
                            "confidence": inference_result.confidence,
                            "boundary_supports": inference_result.boundary_supports,
                        },
                    },
                )
            
            schema = inference_result.schema
            
            # Step 4: Convert to Silver model
            file_spec, fields, record_layouts = fixed_width_to_silver(
                schema,
                name=extract_name_from_uri(uri),
                source_system_id=None,  # Set during persistence
            )
            
            # Step 5: Build warnings list
            all_warnings = list(inference_result.warnings)
            
            # Add low-confidence warning if applicable
            if inference_result.confidence < config.infer_warn:
                all_warnings.append(
                    f"Low confidence inference ({inference_result.confidence:.2f}). "
                    f"Review field boundaries before production use. "
                    f"{config.remediation_explicit_colspec}"
                )
            
            return ParsedSpec(
                source_type=SourceType.FILE,
                source_uri=uri,
                data={
                    "file_spec": file_spec,
                    "fields": fields,
                    "record_layouts": record_layouts,
                },
                warnings=all_warnings,
                confidence=inference_result.confidence,
                metadata={
                    "line_length": schema.line_length,
                    "field_count": len(schema.fields),
                    "has_header": schema.has_header,
                    "detection": {
                        "matched": detection_result.matched,
                        "confidence": detection_result.confidence,
                        "signals": detection_result.signals,
                    },
                    "inference": {
                        "confidence": inference_result.confidence,
                        "boundary_supports": inference_result.boundary_supports,
                    },
                },
            )
            
        except Exception as e:
            logger.error(f"Fixed-width parse error: {e}", exc_info=True)
            return ParsedSpec(
                source_type=SourceType.FILE,
                source_uri=uri,
                data=None,
                errors=[f"Fixed-width parse error: {e}"],
            )


def fixed_width_to_silver(
    schema,
    name: str,
    source_system_id: Optional[int] = None,
) -> Tuple["FileSpec", List["FileField"], List["RecordLayout"]]:
    """
    Convert FixedWidthSchema to Silver domain models.
    
    Args:
        schema: FixedWidthSchema from fixed_width_parser.py
        name: Name for the file spec
        source_system_id: Optional source system ID
        
    Returns:
        Tuple of (FileSpec, List[FileField], List[RecordLayout])
    """
    from integration_coworker.domain.models import (
        FileField,
        FileSpec,
        FileType,
        RecordLayout,
    )
    
    file_spec = FileSpec(
        id=None,
        source_system_id=source_system_id,
        name=name,
        file_type=FileType.FIXED_WIDTH.value,
        has_header=schema.has_header,
        encoding=schema.encoding,
        line_terminator=schema.line_terminator,
        description=f"Fixed-width file, line length {schema.line_length} characters",
    )
    
    fields = []
    for i, f in enumerate(schema.fields):
        # Use field's inference_confidence if available
        confidence = getattr(f, 'inference_confidence', 0.8)
        
        field = FileField(
            id=None,
            file_spec_id=None,  # Set during persistence
            name=f.name,
            field_type=_map_fixed_type_to_file_type(f.inferred_type),
            position=i,
            start_position=f.start,
            length=f.length,
            nullable=f.nullable,
            sample_values=f.sample_values[:5] if f.sample_values else None,
            inference_confidence=confidence,
        )
        fields.append(field)
    
    # Create record layout for this file
    # Support for multi-layout files (header/detail/trailer)
    record_type = getattr(schema, 'record_type', 'detail') or 'detail'
    
    # Check if schema has record_type_position for multi-layout discrimination
    record_type_pos = getattr(schema, 'record_type_position', None)
    identifier_field = None
    identifier_value = None
    
    if record_type_pos is not None:
        start, length = record_type_pos
        identifier_field = f"position_{start}_{start + length}"
        # For single-layout files, we don't set identifier_value
        # For multi-layout, this would be set per layout
    
    record_layout = RecordLayout(
        id=None,
        file_spec_id=None,  # Set during persistence
        record_type=record_type,
        record_length=schema.line_length,
        identifier_field=identifier_field,
        identifier_value=identifier_value,
        position=0,  # Primary layout
        min_occurrences=1,
        description=f"Primary record layout ({schema.line_length} characters)",
    )
    
    return file_spec, fields, [record_layout]


def _map_fixed_type_to_file_type(fixed_type: str) -> str:
    """Map fixed-width parser type to FileFieldType."""
    type_map = {
        "string": "string",
        "integer": "integer",
        "decimal": "decimal",
        "date": "date",
        "datetime": "datetime",
        "boolean": "boolean",
    }
    return type_map.get(fixed_type.lower(), "string")


# =============================================================================
# Multi-Layout Support
# =============================================================================

def detect_record_types(
    content: str,
    discriminator_slice: Tuple[int, int],
) -> Dict[str, List[str]]:
    """
    Detect multiple record types in a fixed-width file.
    
    Many fixed-width files have multiple record types distinguished by
    a type indicator in the first 1-2 characters (or configurable position).
    
    Args:
        content: File content
        discriminator_slice: (start, end) position for record type indicator
        
    Returns:
        Dict mapping record_type_value -> list of matching lines
    """
    start, end = discriminator_slice
    lines = content.split('\n')
    non_empty = [line.rstrip('\r') for line in lines if line.strip()]
    
    record_types: Dict[str, List[str]] = {}
    
    for line in non_empty:
        if len(line) >= end:
            type_value = line[start:end].strip()
            if type_value:
                if type_value not in record_types:
                    record_types[type_value] = []
                record_types[type_value].append(line)
    
    return record_types


def infer_multi_layout_schemas(
    content: str,
    discriminator_slice: Tuple[int, int] = (0, 1),
    config=None,
) -> Dict[str, "FixedWidthSchema"]:
    """
    Infer schemas for multiple record types in a file.
    
    Useful for files with header/detail/trailer records or
    multiple record types identified by a prefix.
    
    Args:
        content: File content
        discriminator_slice: (start, end) for record type indicator
        config: Optional FixedWidthConfidenceConfig
        
    Returns:
        Dict mapping record_type_value -> FixedWidthSchema
    """
    from integration_coworker.parsers.fixed_width_parser import infer_schema
    from integration_coworker.parsers.fixed_width_config import get_config
    
    config = config or get_config()
    
    # Group lines by record type
    record_types = detect_record_types(content, discriminator_slice)
    
    schemas = {}
    
    for type_value, lines in record_types.items():
        if len(lines) < config.min_rows:
            # Not enough samples for this record type
            logger.debug(
                f"Skipping record type '{type_value}': "
                f"only {len(lines)} lines (need {config.min_rows})"
            )
            continue
        
        # Create content subset for this record type
        subset_content = '\n'.join(lines)
        
        # Infer schema for this subset
        result = infer_schema(subset_content, config=config)
        
        if result.is_valid():
            schema = result.schema
            schema.record_type = type_value
            schema.record_type_position = discriminator_slice
            schemas[type_value] = schema
        else:
            logger.warning(
                f"Could not infer schema for record type '{type_value}': "
                f"{result.errors}"
            )
    
    return schemas


__all__ = [
    "FixedWidthSource",
    "fixed_width_to_silver",
    "detect_record_types",
    "infer_multi_layout_schemas",
]
