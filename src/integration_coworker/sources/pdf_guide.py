"""
PDF guide source handler with confidence-gated detection.

Implements the SpecSource protocol for PDF file guides with:
- Rich confidence scoring (no silent heuristics)
- Explicit warnings propagated to ParsedSpec
- Field definition extraction from documentation
- Hard rejection of scanned/image PDFs

Detection uses weighted signals:
- PDF signature (%PDF magic bytes)
- Text extraction success
- Guide-specific keywords
- Field table pattern detection

Extraction outputs guide_fields that can enrich FileSpecs.

All thresholds centralized in pdf_guide_config.py
Per docs/FILE_INTEGRATION_V1_PLAN.md Section 3.3
"""

import logging
from typing import Any, Dict, List, Optional, Union

from .base import (
    ContentType,
    ParsedSpec,
    SourceType,
    extract_name_from_uri,
    normalize_content_bytes,
)

logger = logging.getLogger(__name__)

# PDF content types for signal boost
PDF_CONTENT_TYPES = {
    "application/pdf",
    "application/x-pdf",
}


class PDFGuideSource:
    """
    Handler for PDF file guides with confidence-gated detection.
    
    Detection: Uses detect_with_confidence() which returns explicit
    scores and reasons. Only matches if confidence >= DETECT_MIN.
    
    Parsing: Uses extract_field_definitions() which returns confidence
    and warnings. Warnings propagate to ParsedSpec for downstream visibility.
    
    Outputs guide_fields that can be used to:
    - Create FileSpec + FileField definitions
    - Enrich existing FileSpecs with documentation
    - Validate file structures
    
    Note: This is for file format documentation, NOT API spec PDFs.
    Use the existing pdf_parser.py for API endpoint extraction.
    """
    
    def detect(
        self,
        content: ContentType,
        uri: str,
        content_type: str,
    ) -> float:
        """
        Detect if content is a PDF guide with field definitions.
        
        Returns confidence score 0-1.
        
        Internally uses detect_with_confidence() with weighted signals:
        - PDF signature (30%)
        - Text extraction (25%)
        - Guide keywords (20%)
        - Field table patterns (25%)
        
        Hard rejects (returns 0.0):
        - Missing %PDF signature
        - Scanned/image PDFs (no extractable text)
        """
        content_bytes = normalize_content_bytes(content)
        
        # Quick reject: check PDF signature first
        if not content_bytes[:4] == b"%PDF":
            return 0.0
        
        # Reject non-PDF extensions explicitly
        uri_lower = uri.lower()
        if any(uri_lower.endswith(ext) for ext in ['.xlsx', '.xls', '.csv', '.json', '.xml', '.txt']):
            return 0.0
        
        try:
            from integration_coworker.parsers.pdf_guide_parser import (
                detect_with_confidence,
            )
            from integration_coworker.parsers.pdf_guide_config import get_config
            
            config = get_config()
            result = detect_with_confidence(content_bytes, uri, config)
            
            # Boost if explicit PDF content type provided
            if content_type and content_type.lower() in PDF_CONTENT_TYPES:
                # Content type confirmation adds confidence
                if not result.matched and result.confidence > 0.5:
                    # Borderline case - boost slightly
                    result.confidence = min(result.confidence + 0.1, 1.0)
            
            logger.debug(
                f"PDFGuideSource detection for {uri}: "
                f"matched={result.matched}, confidence={result.confidence:.2f}, "
                f"reasons={result.reasons}"
            )
            
            return result.confidence
            
        except ImportError as e:
            logger.warning(f"PDF guide parser not available: {e}")
            return 0.0
        except Exception as e:
            logger.warning(f"PDF guide detection error: {e}")
            return 0.0
    
    def parse(
        self,
        content: ContentType,
        uri: str,
    ) -> ParsedSpec:
        """
        Parse PDF guide and extract field definitions.
        
        Returns ParsedSpec with:
        - source_type=FILE
        - data: {
            guide_fields: List of extracted field definitions
            source_name: Cleaned name from URI
            extracted_text: Raw text for debugging (optional)
          }
        - warnings: Propagated from extraction
        - confidence: From extraction
        - metadata: Detection/extraction breakdown for debugging
        
        If extraction confidence < INFER_MIN:
        - Returns invalid ParsedSpec with errors
        
        If extraction confidence < INFER_WARN:
        - Returns valid ParsedSpec with warnings
        """
        content_bytes = normalize_content_bytes(content)
        
        try:
            from integration_coworker.parsers.pdf_guide_parser import (
                detect_with_confidence,
                extract_field_definitions,
            )
            from integration_coworker.parsers.pdf_guide_config import get_config
        except ImportError as e:
            return ParsedSpec(
                source_type=SourceType.FILE,
                source_uri=uri,
                data=None,
                errors=[f"PDF guide parser not available: {e}. Install with: pip install pypdf"],
            )
        
        config = get_config()
        
        try:
            # Step 1: Run detection for metadata
            detection_result = detect_with_confidence(content_bytes, uri, config)
            
            # Step 2: Run field extraction
            extraction_result = extract_field_definitions(content_bytes, config)
            
            # Step 3: Check for hard failure
            if extraction_result.errors:
                return ParsedSpec(
                    source_type=SourceType.FILE,
                    source_uri=uri,
                    data=None,
                    errors=extraction_result.errors,
                    warnings=extraction_result.warnings,
                    confidence=extraction_result.confidence,
                    metadata={
                        "detection": detection_result.to_dict(),
                        "extraction": extraction_result.metadata,
                    },
                )
            
            # Step 4: Check confidence threshold
            if extraction_result.confidence < config.infer_min:
                return ParsedSpec(
                    source_type=SourceType.FILE,
                    source_uri=uri,
                    data=None,
                    errors=[
                        f"Extraction confidence ({extraction_result.confidence:.2f}) "
                        f"below minimum ({config.infer_min})"
                    ],
                    warnings=extraction_result.warnings,
                    confidence=extraction_result.confidence,
                    metadata={
                        "detection": detection_result.to_dict(),
                        "extraction": extraction_result.metadata,
                    },
                )
            
            # Step 5: Convert to output format
            base_name = extract_name_from_uri(uri)
            
            # Convert ExtractedField to serializable format
            guide_fields = [
                {
                    "name": f.name,
                    "field_type": f.field_type,
                    "position": f.position,
                    "start_position": f.start_position,
                    "length": f.length,
                    "description": f.description,
                    "confidence": f.confidence,
                    "format_mask": f.format_mask,
                    "nullable": f.nullable,
                }
                for f in extraction_result.fields
            ]
            
            # Convert to FileSpec + FileField if we have structured data
            file_spec = None
            fields = None
            
            if extraction_result.fields:
                file_spec, fields = _guide_fields_to_silver(
                    extraction_result.fields,
                    base_name,
                    source_system_id=None,
                )
            
            # Step 6: Build warnings list
            all_warnings = list(extraction_result.warnings)
            
            if extraction_result.confidence < config.infer_warn:
                all_warnings.append(
                    f"Low confidence extraction ({extraction_result.confidence:.2f}). "
                    f"Review field definitions before production use."
                )
            
            return ParsedSpec(
                source_type=SourceType.FILE,
                source_uri=uri,
                data={
                    "guide_fields": guide_fields,
                    "file_spec": file_spec,
                    "fields": fields,
                    "source_name": base_name,
                },
                warnings=all_warnings,
                confidence=extraction_result.confidence,
                metadata={
                    "field_count": len(extraction_result.fields),
                    "page_count": detection_result.page_count,
                    "detection": {
                        "matched": detection_result.matched,
                        "confidence": detection_result.confidence,
                        "signals": detection_result.signals,
                    },
                    "extraction": extraction_result.metadata,
                },
            )
            
        except Exception as e:
            logger.error(f"PDF guide parse error: {e}", exc_info=True)
            return ParsedSpec(
                source_type=SourceType.FILE,
                source_uri=uri,
                data=None,
                errors=[f"PDF guide parse error: {e}"],
            )


def _guide_fields_to_silver(
    extracted_fields: List[Any],
    base_name: str,
    source_system_id: Optional[int] = None,
) -> tuple:
    """
    Convert extracted guide fields to Silver domain models.
    
    Args:
        extracted_fields: List of ExtractedField from pdf_guide_parser
        base_name: Base name for the file spec
        source_system_id: Optional source system ID
        
    Returns:
        Tuple of (FileSpec, List[FileField])
    """
    from integration_coworker.domain.models import (
        FileField,
        FileSpec,
        FileType,
    )
    
    # Determine file type from fields
    # If we see start_position/length, it's likely fixed-width
    has_positions = any(f.start_position is not None for f in extracted_fields)
    file_type = FileType.FIXED_WIDTH if has_positions else FileType.OTHER_DELIMITED
    
    file_spec = FileSpec(
        id=None,
        source_system_id=source_system_id,
        name=base_name,
        file_type=file_type.value,
        has_header=not has_positions,  # Fixed-width usually doesn't have headers
        encoding="utf-8",
        description=f"Extracted from PDF guide: {len(extracted_fields)} fields",
    )
    
    fields = []
    for i, f in enumerate(extracted_fields):
        field = FileField(
            id=None,
            file_spec_id=None,
            name=f.name,
            field_type=f.field_type,
            position=f.position if f.position is not None else i,
            start_position=f.start_position,
            length=f.length,
            format_mask=f.format_mask,
            nullable=f.nullable,
            description=f.description,
            inference_confidence=f.confidence,
        )
        fields.append(field)
    
    return file_spec, fields


__all__ = [
    "PDFGuideSource",
]
