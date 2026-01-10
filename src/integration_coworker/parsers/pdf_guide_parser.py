"""
PDF guide detection and field extraction with confidence scoring.

This module provides confidence-gated detection and extraction of field
definitions from PDF file guides/specifications. Unlike the existing 
pdf_parser.py (which extracts API endpoints), this module focuses on
extracting field definitions (name, type, position, length) from PDF 
documentation that describes file formats.

Detection uses weighted signals:
- PDF signature (%PDF magic bytes)
- Text extraction success
- Guide-specific keywords
- Field table pattern detection

Extraction uses:
- Table-like pattern recognition
- Keyword-based section detection
- Position/length pattern extraction

All thresholds centralized in pdf_guide_config.py
"""

import io
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .pdf_guide_config import (
    PDFGuideConfidenceConfig,
    GUIDE_KEYWORDS,
    FIELD_TABLE_PATTERNS,
    get_config,
)

logger = logging.getLogger(__name__)


@dataclass
class PDFDetectionResult:
    """
    Result of PDF guide detection with explicit signals.
    
    Exposes all signals for debugging and testing.
    """
    matched: bool
    confidence: float
    signals: Dict[str, float]
    reasons: List[str]
    extracted_text: Optional[str] = None
    page_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for metadata."""
        return {
            "matched": self.matched,
            "confidence": self.confidence,
            "signals": self.signals,
            "reasons": self.reasons,
            "page_count": self.page_count,
        }


@dataclass
class ExtractedField:
    """
    A field definition extracted from PDF.
    
    Contains all information that can be reliably extracted:
    - name: Field name
    - field_type: Detected type (string, integer, date, etc.)
    - position: 0-indexed position (if detectable)
    - start_position: Start byte/char for fixed-width
    - length: Field length for fixed-width
    - description: Description text if found
    - confidence: Confidence in this extraction
    """
    name: str
    field_type: str = "string"
    position: Optional[int] = None
    start_position: Optional[int] = None
    length: Optional[int] = None
    description: Optional[str] = None
    confidence: float = 0.5
    format_mask: Optional[str] = None
    nullable: bool = True


@dataclass
class PDFGuideExtractionResult:
    """
    Result of field extraction from PDF guide.
    """
    fields: List[ExtractedField]
    confidence: float
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def is_valid(self) -> bool:
        """Check if extraction succeeded."""
        return len(self.errors) == 0 and len(self.fields) > 0


def detect_with_confidence(
    content: bytes,
    uri: str,
    config: Optional[PDFGuideConfidenceConfig] = None,
) -> PDFDetectionResult:
    """
    Detect if content is a parseable PDF guide with explicit signals.
    
    Weighted signals:
    - pdf_signature (30%): %PDF magic bytes present
    - text_extraction (25%): Text can be extracted
    - guide_keywords (20%): Guide-specific keywords found
    - field_table_pattern (25%): Field table patterns detected
    
    Hard rejects:
    - Missing PDF signature → confidence 0.0
    - No extractable text (scanned PDF) → confidence capped at 0.3
    
    Args:
        content: Raw PDF bytes
        uri: Source URI for logging
        config: Optional config override (uses default if None)
        
    Returns:
        PDFDetectionResult with signals and confidence
    """
    if config is None:
        config = get_config()
    
    signals = {
        "pdf_signature": 0.0,
        "text_extraction": 0.0,
        "guide_keywords": 0.0,
        "field_table_pattern": 0.0,
    }
    reasons = []
    
    # Signal 1: PDF signature check
    if content[:4] == b"%PDF":
        signals["pdf_signature"] = 1.0
        reasons.append("pdf_signature=1.00 (%PDF magic bytes present)")
    else:
        reasons.append("pdf_signature=0.00 (missing %PDF magic bytes)")
        # Hard reject: not a PDF at all
        return PDFDetectionResult(
            matched=False,
            confidence=0.0,
            signals=signals,
            reasons=reasons,
        )
    
    # Signal 2-4: Try to extract text for further analysis
    try:
        extracted_text, page_count = _extract_pdf_text(content, config)
    except Exception as e:
        reasons.append(f"text_extraction=0.00 (extraction failed: {e})")
        # Can't analyze without text
        confidence = signals["pdf_signature"] * config.weight_pdf_signature
        return PDFDetectionResult(
            matched=confidence >= config.detect_min,
            confidence=confidence,
            signals=signals,
            reasons=reasons,
        )
    
    # Signal 2: Text extraction success
    text_length = len(extracted_text.strip()) if extracted_text else 0
    avg_text_per_page = text_length / max(page_count, 1)
    
    if text_length >= config.min_text_length:
        signals["text_extraction"] = 1.0
        reasons.append(f"text_extraction=1.00 ({text_length} chars extracted)")
    elif text_length > 0:
        # Partial credit
        ratio = min(text_length / config.min_text_length, 1.0)
        signals["text_extraction"] = ratio
        reasons.append(f"text_extraction={ratio:.2f} (only {text_length} chars)")
    else:
        reasons.append("text_extraction=0.00 (no text extracted)")
    
    # Hard reject: scanned PDF (very little text per page)
    if page_count > 0 and avg_text_per_page < config.scanned_pdf_text_threshold:
        reasons.append(f"HARD_REJECT: likely scanned PDF ({avg_text_per_page:.1f} chars/page)")
        return PDFDetectionResult(
            matched=False,
            confidence=min(0.30, signals["pdf_signature"] * 0.3),
            signals=signals,
            reasons=reasons,
            extracted_text=extracted_text,
            page_count=page_count,
        )
    
    # Signal 3: Guide keywords check
    text_lower = extracted_text.lower() if extracted_text else ""
    keyword_matches = sum(1 for kw in GUIDE_KEYWORDS if kw in text_lower)
    
    if keyword_matches >= 5:
        signals["guide_keywords"] = 1.0
        reasons.append(f"guide_keywords=1.00 ({keyword_matches} keywords found)")
    elif keyword_matches >= 2:
        signals["guide_keywords"] = 0.6 + (keyword_matches - 2) * 0.1
        reasons.append(f"guide_keywords={signals['guide_keywords']:.2f} ({keyword_matches} keywords)")
    else:
        signals["guide_keywords"] = keyword_matches * 0.25
        reasons.append(f"guide_keywords={signals['guide_keywords']:.2f} ({keyword_matches} keywords)")
    
    # Signal 4: Field table pattern detection
    pattern_matches = 0
    for pattern in FIELD_TABLE_PATTERNS:
        if re.search(pattern, text_lower):
            pattern_matches += 1
    
    if pattern_matches >= 3:
        signals["field_table_pattern"] = 1.0
        reasons.append(f"field_table_pattern=1.00 ({pattern_matches} patterns matched)")
    elif pattern_matches >= 1:
        signals["field_table_pattern"] = 0.4 + pattern_matches * 0.2
        reasons.append(f"field_table_pattern={signals['field_table_pattern']:.2f} ({pattern_matches} patterns)")
    else:
        reasons.append("field_table_pattern=0.00 (no table patterns found)")
    
    # Calculate weighted confidence
    confidence = (
        signals["pdf_signature"] * config.weight_pdf_signature +
        signals["text_extraction"] * config.weight_text_extraction +
        signals["guide_keywords"] * config.weight_guide_keywords +
        signals["field_table_pattern"] * config.weight_field_table
    )
    
    return PDFDetectionResult(
        matched=confidence >= config.detect_min,
        confidence=confidence,
        signals=signals,
        reasons=reasons,
        extracted_text=extracted_text,
        page_count=page_count,
    )


def extract_field_definitions(
    content: bytes,
    config: Optional[PDFGuideConfidenceConfig] = None,
) -> PDFGuideExtractionResult:
    """
    Extract field definitions from PDF guide content.
    
    This function attempts to extract structured field information from
    PDF documentation describing file formats. It uses multiple strategies:
    
    1. Table-based extraction: Look for tabular field definitions
    2. Pattern-based extraction: Match common field definition patterns
    3. Keyword-based extraction: Find field info near keywords
    
    Args:
        content: Raw PDF bytes
        config: Optional config override
        
    Returns:
        PDFGuideExtractionResult with extracted fields and confidence
    """
    if config is None:
        config = get_config()
    
    result = PDFGuideExtractionResult(
        fields=[],
        confidence=0.0,
        warnings=[],
        errors=[],
        metadata={},
    )
    
    # First, extract text
    try:
        text, page_count = _extract_pdf_text(content, config)
    except Exception as e:
        result.errors.append(f"Failed to extract text: {e}")
        return result
    
    if not text or len(text.strip()) < config.min_text_length:
        result.errors.append(f"Insufficient text extracted ({len(text) if text else 0} chars)")
        return result
    
    result.metadata["page_count"] = page_count
    result.metadata["text_length"] = len(text)
    
    # Strategy 1: Try table-based extraction
    table_fields = _extract_fields_from_tables(text, config)
    
    # Strategy 2: Try pattern-based extraction
    pattern_fields = _extract_fields_from_patterns(text, config)
    
    # Strategy 3: Try keyword-based extraction
    keyword_fields = _extract_fields_from_keywords(text, config)
    
    # Merge results, preferring higher-confidence extractions
    all_fields: Dict[str, ExtractedField] = {}
    
    for field_list, source in [
        (table_fields, "table"),
        (pattern_fields, "pattern"),
        (keyword_fields, "keyword"),
    ]:
        for f in field_list:
            key = f.name.lower().strip()
            if key not in all_fields or f.confidence > all_fields[key].confidence:
                all_fields[key] = f
                logger.debug(f"Added field '{f.name}' from {source} (confidence={f.confidence:.2f})")
    
    result.fields = list(all_fields.values())
    
    # Calculate overall confidence
    if result.fields:
        avg_confidence = sum(f.confidence for f in result.fields) / len(result.fields)
        # Boost confidence if we found multiple fields
        field_count_boost = min(len(result.fields) / 10, 0.2)
        result.confidence = min(avg_confidence + field_count_boost, 1.0)
    else:
        result.confidence = 0.0
        result.warnings.append("No field definitions could be extracted")
    
    # Add warnings for low confidence
    if result.confidence < config.infer_warn and result.fields:
        result.warnings.append(
            f"Low confidence extraction ({result.confidence:.2f}). "
            f"Manual review recommended."
        )
    
    result.metadata["extraction_strategies"] = {
        "table_fields": len(table_fields),
        "pattern_fields": len(pattern_fields),
        "keyword_fields": len(keyword_fields),
        "merged_fields": len(result.fields),
    }
    
    return result


def _extract_pdf_text(
    content: bytes,
    config: PDFGuideConfidenceConfig,
) -> Tuple[str, int]:
    """
    Extract text from PDF content.
    
    Args:
        content: Raw PDF bytes
        config: Configuration for extraction limits
        
    Returns:
        Tuple of (extracted_text, page_count)
        
    Raises:
        ImportError: If pypdf is not available
        Exception: If PDF parsing fails
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError("pypdf is required: pip install pypdf")
    
    reader = PdfReader(io.BytesIO(content))
    page_count = len(reader.pages)
    
    text_parts = []
    pages_to_scan = min(page_count, config.max_pages_to_scan)
    
    for i in range(pages_to_scan):
        try:
            page_text = reader.pages[i].extract_text() or ""
            text_parts.append(page_text)
        except Exception as e:
            logger.debug(f"Failed to extract page {i}: {e}")
    
    return "\n\n".join(text_parts), page_count


def _extract_fields_from_tables(
    text: str,
    config: PDFGuideConfidenceConfig,
) -> List[ExtractedField]:
    """
    Extract field definitions from table-like structures.
    
    Looks for patterns like:
    - "Field Name | Type | Length | Position"
    - Rows with consistent column-like spacing
    """
    fields = []
    
    # Pattern 1: Pipe-delimited tables
    # Match: FieldName | Type | Length | Description
    pipe_pattern = r"([A-Za-z_][A-Za-z0-9_]*)\s*\|\s*(string|integer|decimal|date|datetime|char|varchar|numeric|number)\s*\|\s*(\d+)"
    for match in re.finditer(pipe_pattern, text, re.IGNORECASE):
        name, field_type, length = match.groups()
        fields.append(ExtractedField(
            name=name,
            field_type=_normalize_type(field_type),
            length=int(length),
            confidence=0.8,
        ))
    
    # Pattern 2: Position-based field definitions
    # Match: CUSTOMER_ID   1    10   CHAR   Customer identifier
    pos_pattern = r"([A-Z][A-Z0-9_]{1,30})\s+(\d+)\s+(\d+)\s+(CHAR|VARCHAR|NUM|NUMERIC|DATE|INTEGER|DECIMAL|STRING)"
    for match in re.finditer(pos_pattern, text, re.IGNORECASE):
        name, start, length, field_type = match.groups()
        fields.append(ExtractedField(
            name=name,
            field_type=_normalize_type(field_type),
            start_position=int(start),
            length=int(length),
            confidence=0.85,
        ))
    
    # Pattern 3: Field with start-end positions
    # Match: ACCOUNT_NUMBER  positions 1-20  alphanumeric
    range_pattern = r"([A-Z][A-Z0-9_]{1,30})\s+(?:position|pos|bytes?|chars?)\s*[\s:=]*(\d+)\s*[-–to]+\s*(\d+)"
    for match in re.finditer(range_pattern, text, re.IGNORECASE):
        name, start, end = match.groups()
        start_pos = int(start)
        end_pos = int(end)
        fields.append(ExtractedField(
            name=name,
            start_position=start_pos,
            length=end_pos - start_pos + 1,
            confidence=0.75,
        ))
    
    return fields


def _extract_fields_from_patterns(
    text: str,
    config: PDFGuideConfidenceConfig,
) -> List[ExtractedField]:
    """
    Extract field definitions using common documentation patterns.
    """
    fields = []
    
    # Pattern: Field definitions with type and length
    # "customer_id: INTEGER(10)"
    # "first_name: VARCHAR(50)"
    type_len_pattern = r"([a-zA-Z_][a-zA-Z0-9_]*)\s*[:\-=]\s*(INTEGER|VARCHAR|CHAR|DECIMAL|DATE|DATETIME|NUMERIC|STRING)\s*\(\s*(\d+)"
    for match in re.finditer(type_len_pattern, text, re.IGNORECASE):
        name, field_type, length = match.groups()
        if config.field_name_min_length <= len(name) <= config.field_name_max_length:
            fields.append(ExtractedField(
                name=name,
                field_type=_normalize_type(field_type),
                length=int(length),
                confidence=0.7,
            ))
    
    # Pattern: Length specifications
    # "customer_name (max 100 characters)"
    max_len_pattern = r"([a-zA-Z_][a-zA-Z0-9_]*)\s*\((?:max|maximum|up to)\s+(\d+)\s*(?:characters?|chars?|bytes?)\)"
    for match in re.finditer(max_len_pattern, text, re.IGNORECASE):
        name, length = match.groups()
        if config.field_name_min_length <= len(name) <= config.field_name_max_length:
            fields.append(ExtractedField(
                name=name,
                field_type="string",
                length=int(length),
                confidence=0.6,
            ))
    
    # Pattern: Fixed-width field specs
    # "Bytes 1-10: Record Type"
    bytes_pattern = r"(?:bytes?|positions?|chars?)\s+(\d+)\s*[-–]\s*(\d+)\s*[:\-=]\s*([A-Za-z_][A-Za-z0-9_\s]{1,40})"
    for match in re.finditer(bytes_pattern, text, re.IGNORECASE):
        start, end, name = match.groups()
        # Clean up name
        name = re.sub(r"\s+", "_", name.strip())
        name = re.sub(r"[^A-Za-z0-9_]", "", name)
        if config.field_name_min_length <= len(name) <= config.field_name_max_length:
            fields.append(ExtractedField(
                name=name,
                start_position=int(start),
                length=int(end) - int(start) + 1,
                confidence=0.75,
            ))
    
    return fields


def _extract_fields_from_keywords(
    text: str,
    config: PDFGuideConfidenceConfig,
) -> List[ExtractedField]:
    """
    Extract field definitions by looking near guide keywords.
    """
    fields = []
    
    # Look for "Field Name" followed by a list
    field_section_pattern = r"(?:field\s*name|column\s*name|data\s*element)[s]?\s*[:\-]?\s*\n((?:[A-Z][A-Z0-9_]*\s*\n?)+)"
    for match in re.finditer(field_section_pattern, text, re.IGNORECASE):
        field_block = match.group(1)
        field_names = re.findall(r"([A-Z][A-Z0-9_]{1,30})", field_block)
        for i, name in enumerate(field_names):
            if config.field_name_min_length <= len(name) <= config.field_name_max_length:
                fields.append(ExtractedField(
                    name=name,
                    position=i,
                    confidence=0.5,
                ))
    
    # Look for "Required fields:" sections
    required_pattern = r"required\s*(?:field|column)s?\s*[:\-]?\s*([A-Za-z_,\s]+)"
    for match in re.finditer(required_pattern, text, re.IGNORECASE):
        field_list = match.group(1)
        field_names = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)", field_list)
        for name in field_names:
            if config.field_name_min_length <= len(name) <= config.field_name_max_length:
                existing = next((f for f in fields if f.name.lower() == name.lower()), None)
                if existing:
                    existing.nullable = False
                else:
                    fields.append(ExtractedField(
                        name=name,
                        nullable=False,
                        confidence=0.45,
                    ))
    
    return fields


def _normalize_type(raw_type: str) -> str:
    """
    Normalize extracted type to standard FileFieldType.
    """
    type_map = {
        "string": "string",
        "varchar": "string",
        "char": "string",
        "text": "string",
        "integer": "integer",
        "int": "integer",
        "numeric": "decimal",
        "number": "decimal",
        "decimal": "decimal",
        "float": "decimal",
        "double": "decimal",
        "date": "date",
        "datetime": "datetime",
        "timestamp": "datetime",
        "boolean": "boolean",
        "bool": "boolean",
        "num": "decimal",
    }
    return type_map.get(raw_type.lower(), "string")


__all__ = [
    "PDFDetectionResult",
    "ExtractedField",
    "PDFGuideExtractionResult",
    "detect_with_confidence",
    "extract_field_definitions",
]
