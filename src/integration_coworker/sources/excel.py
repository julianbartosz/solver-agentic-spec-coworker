"""
Excel file source handler with confidence-gated detection.

Implements the SpecSource protocol for Excel files (XLSX/XLS) with:
- Rich confidence scoring (no silent heuristics)
- Explicit warnings propagated to ParsedSpec
- One FileSpec per sheet (cleanest mapping)
- Production-safe detection that won't claim CSV/text as Excel

Detection uses weighted signals:
- ZIP/OLE2 signature validation
- Content-type hints
- Extension hints
- openpyxl load success

All thresholds centralized in excel_config.py
"""

import logging
from typing import Dict, List, Optional, Tuple, Union

from .base import (
    ContentType,
    ParsedSpec,
    SourceType,
    extract_name_from_uri,
    normalize_content_bytes,
)

logger = logging.getLogger(__name__)


# Excel-related content types for signal boost
EXCEL_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    "application/vnd.ms-excel",  # .xls
    "application/x-excel",
    "application/x-msexcel",
}


class ExcelSource:
    """
    Handler for Excel files with confidence-gated detection.
    
    Detection: Uses detect_with_confidence() which returns explicit
    scores and reasons. Only matches if confidence >= DETECT_MIN.
    
    Parsing: Uses infer_schema() which returns confidence and warnings.
    Warnings propagate to ParsedSpec for downstream visibility.
    
    Outputs one FileSpec per sheet, each with FileField + RecordLayout
    for the Silver File Model.
    """
    
    def detect(
        self,
        content: ContentType,
        uri: str,
        content_type: str,
    ) -> float:
        """
        Detect if content is Excel format.
        
        Returns confidence score 0-1.
        
        Internally uses detect_with_confidence() with weighted signals:
        - ZIP signature (40%)
        - Content-type hint (20%)
        - Extension hint (25%)
        - Workbook load test (15%)
        
        Hard rejects (returns 0.0):
        - Plain text content (high printable ratio)
        - CSV/TSV/JSON extensions
        - Failed workbook load
        """
        # Early reject for clearly non-Excel extensions
        uri_lower = uri.lower()
        if any(uri_lower.endswith(ext) for ext in ['.csv', '.tsv', '.json', '.xml', '.yaml', '.yml', '.txt']):
            return 0.0
        
        content_bytes = normalize_content_bytes(content)
        
        try:
            from integration_coworker.parsers.excel_parser import (
                detect_with_confidence,
            )
            from integration_coworker.parsers.excel_config import get_config
            
            config = get_config()
            result = detect_with_confidence(content_bytes, uri, config)
            
            # Boost content_type signal if caller provides matching type
            if content_type and content_type.lower() in EXCEL_CONTENT_TYPES:
                # Recalculate with content_type signal boosted
                result.signals["content_type"] = 1.0
                result.reasons.append("content_type=1.00 (explicit Excel MIME type)")
                
                # Recalculate confidence
                confidence = (
                    result.signals["zip_signature"] * config.weight_zip_signature +
                    result.signals["content_type"] * config.weight_content_type +
                    result.signals["extension_hint"] * config.weight_extension_hint +
                    result.signals["workbook_load"] * config.weight_workbook_load
                )
                result.confidence = confidence
            
            logger.debug(
                f"ExcelSource detection for {uri}: "
                f"matched={result.matched}, confidence={result.confidence:.2f}, "
                f"reasons={result.reasons}"
            )
            
            # Return raw confidence (router will compare against threshold)
            return result.confidence
            
        except ImportError as e:
            logger.warning(f"Excel parser not available: {e}")
            return 0.0
        except Exception as e:
            logger.warning(f"Excel detection error: {e}")
            return 0.0
    
    def parse(
        self,
        content: ContentType,
        uri: str,
    ) -> ParsedSpec:
        """
        Parse Excel content with explicit confidence and warnings.
        
        Returns ParsedSpec with:
        - source_type=FILE
        - data: {file_specs: [...], fields: [...], record_layouts: [...]}
          (One file_spec per sheet)
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
        content_bytes = normalize_content_bytes(content)
        
        try:
            from integration_coworker.parsers.excel_parser import (
                detect_with_confidence,
                infer_schema,
            )
            from integration_coworker.parsers.excel_config import get_config
        except ImportError as e:
            return ParsedSpec(
                source_type=SourceType.FILE,
                source_uri=uri,
                data=None,
                errors=[f"Excel parser not available: {e}. Install with: pip install openpyxl"],
            )
        
        config = get_config()
        
        try:
            # Step 1: Run detection for metadata
            detection_result = detect_with_confidence(content_bytes, uri, config)
            
            # Step 2: Run inference with confidence scoring
            inference_result = infer_schema(content_bytes, config=config)
            
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
                    },
                )
            
            # Step 4: Convert to Silver model (one FileSpec per sheet)
            base_name = extract_name_from_uri(uri)
            
            all_file_specs = []
            all_fields = []
            all_record_layouts = []
            
            for sheet_schema in inference_result.sheets:
                file_spec, fields, record_layout = _sheet_to_silver(
                    sheet_schema,
                    base_name=base_name,
                    source_system_id=None,  # Set during persistence
                )
                all_file_specs.append(file_spec)
                all_fields.extend(fields)
                all_record_layouts.append(record_layout)
            
            # Step 5: Build warnings list
            all_warnings = list(inference_result.warnings)
            
            # Add low-confidence warning if applicable
            if inference_result.confidence < config.infer_warn:
                all_warnings.append(
                    f"Low confidence inference ({inference_result.confidence:.2f}). "
                    f"Review column definitions before production use."
                )
            
            # Single sheet: use simpler structure
            if len(all_file_specs) == 1:
                return ParsedSpec(
                    source_type=SourceType.FILE,
                    source_uri=uri,
                    data={
                        "file_spec": all_file_specs[0],
                        "fields": all_fields,
                        "record_layouts": all_record_layouts,
                    },
                    warnings=all_warnings,
                    confidence=inference_result.confidence,
                    metadata={
                        "sheet_count": 1,
                        "sheet_name": inference_result.sheets[0].sheet_name,
                        "detection": {
                            "matched": detection_result.matched,
                            "confidence": detection_result.confidence,
                            "signals": detection_result.signals,
                        },
                        "inference": {
                            "confidence": inference_result.confidence,
                        },
                    },
                )
            
            # Multiple sheets: return list
            return ParsedSpec(
                source_type=SourceType.FILE,
                source_uri=uri,
                data={
                    "file_specs": all_file_specs,
                    "fields": all_fields,
                    "record_layouts": all_record_layouts,
                },
                warnings=all_warnings,
                confidence=inference_result.confidence,
                metadata={
                    "sheet_count": len(all_file_specs),
                    "sheet_names": [s.sheet_name for s in inference_result.sheets],
                    "detection": {
                        "matched": detection_result.matched,
                        "confidence": detection_result.confidence,
                        "signals": detection_result.signals,
                    },
                    "inference": {
                        "confidence": inference_result.confidence,
                    },
                },
            )
            
        except Exception as e:
            logger.error(f"Excel parse error: {e}", exc_info=True)
            return ParsedSpec(
                source_type=SourceType.FILE,
                source_uri=uri,
                data=None,
                errors=[f"Excel parse error: {e}"],
            )


def _sheet_to_silver(
    sheet_schema,
    base_name: str,
    source_system_id: Optional[int] = None,
) -> Tuple["FileSpec", List["FileField"], "RecordLayout"]:
    """
    Convert ExcelSheetSchema to Silver domain models.
    
    Args:
        sheet_schema: ExcelSheetSchema from excel_parser.py
        base_name: Base name for the file spec (from URI)
        source_system_id: Optional source system ID
        
    Returns:
        Tuple of (FileSpec, List[FileField], RecordLayout)
    """
    from integration_coworker.domain.models import (
        FileField,
        FileSpec,
        FileType,
        RecordLayout,
    )
    
    # Create unique name for this sheet
    spec_name = f"{base_name}_{sheet_schema.sheet_name}".lower()
    spec_name = spec_name.replace(" ", "_")
    
    file_spec = FileSpec(
        id=None,
        source_system_id=source_system_id,
        name=spec_name,
        file_type=FileType.XLSX.value,
        has_header=sheet_schema.has_header,
        encoding="utf-8",  # Excel uses UTF-8 internally
        description=f"Excel sheet '{sheet_schema.sheet_name}', {len(sheet_schema.columns)} columns, {sheet_schema.row_count} rows",
    )
    
    fields = []
    for col in sheet_schema.columns:
        field = FileField(
            id=None,
            file_spec_id=None,  # Set during persistence
            name=col.name,
            field_type=_map_excel_type_to_file_type(col.inferred_type),
            position=col.position,
            nullable=col.nullable,
            sample_values=col.sample_values[:5] if col.sample_values else None,
            inference_confidence=col.inference_confidence,
        )
        fields.append(field)
    
    # Create record layout for this sheet
    record_layout = RecordLayout(
        id=None,
        file_spec_id=None,  # Set during persistence
        record_type="detail",  # Excel sheets are typically detail records
        identifier_field=None,
        identifier_value=None,
        position=0,
        min_occurrences=1,
        description=f"Excel sheet '{sheet_schema.sheet_name}'",
    )
    
    return file_spec, fields, record_layout


def _map_excel_type_to_file_type(excel_type: str) -> str:
    """Map Excel parser type to FileFieldType."""
    type_map = {
        "string": "string",
        "integer": "integer",
        "decimal": "decimal",
        "date": "date",
        "datetime": "datetime",
        "boolean": "boolean",
    }
    return type_map.get(excel_type.lower(), "string")


__all__ = [
    "ExcelSource",
]
