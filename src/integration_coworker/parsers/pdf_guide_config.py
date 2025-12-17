"""
Configuration for PDF guide detection and field extraction.

This module centralizes all confidence thresholds, signal weights, and 
detection parameters for the PDFGuideSource. These values are tuned for
production safety:

- Higher DETECT_MIN (0.85) because PDFs are diverse
- Weighted signals favor structural indicators (field tables)
- Explicit hard-reject conditions for scanned/image PDFs

Signal weights (must sum to 1.0):
- pdf_signature (30%): %PDF magic bytes
- text_extraction (25%): Text content extractable
- guide_keywords (20%): Keywords suggesting a file guide
- field_table_pattern (25%): Tabular field definitions detected

Per docs/FILE_INTEGRATION_V1_PLAN.md Section 3.3
"""

from dataclasses import dataclass
from typing import Set


@dataclass(frozen=True)
class PDFGuideConfidenceConfig:
    """
    Immutable configuration for PDF guide detection.
    
    All thresholds and weights are exposed for testing overrides.
    """
    
    # Detection thresholds
    detect_min: float = 0.85
    """Minimum confidence to claim this is a parseable PDF guide."""
    
    # Inference thresholds
    infer_min: float = 0.50
    """Minimum confidence for field extraction to be considered valid."""
    
    infer_warn: float = 0.70
    """Below this, emit warnings but still proceed."""
    
    # Signal weights (must sum to 1.0)
    weight_pdf_signature: float = 0.30
    """Weight for %PDF magic bytes check."""
    
    weight_text_extraction: float = 0.25
    """Weight for successful text extraction."""
    
    weight_guide_keywords: float = 0.20
    """Weight for detecting guide-specific keywords."""
    
    weight_field_table: float = 0.25
    """Weight for detecting field definition table patterns."""
    
    # Text extraction settings
    max_pages_to_scan: int = 20
    """Maximum pages to scan for field definitions."""
    
    min_text_length: int = 100
    """Minimum extracted text length to consider valid."""
    
    min_text_ratio: float = 0.01
    """Minimum ratio of text chars to total chars for non-scanned detection."""
    
    # Field table detection patterns
    field_name_min_length: int = 2
    """Minimum length for field names."""
    
    field_name_max_length: int = 64
    """Maximum length for field names."""
    
    # Hard reject conditions
    scanned_pdf_text_threshold: int = 50
    """If text length per page is below this, likely scanned."""
    
    def __post_init__(self) -> None:
        """Validate that weights sum to 1.0."""
        total = (
            self.weight_pdf_signature +
            self.weight_text_extraction +
            self.weight_guide_keywords +
            self.weight_field_table
        )
        if abs(total - 1.0) > 0.001:
            raise ValueError(f"Signal weights must sum to 1.0, got {total}")


# Guide-specific keywords that suggest this PDF is a file specification guide
# These patterns are common in integration/file format documentation
GUIDE_KEYWORDS: Set[str] = {
    # Field definition terms
    "field name",
    "field description",
    "field type",
    "field length",
    "data type",
    "data element",
    "column name",
    "column description",
    "position",
    "start position",
    "end position",
    "byte offset",
    
    # File format terms
    "file layout",
    "file format",
    "file specification",
    "record layout",
    "record format",
    "fixed width",
    "fixed-width",
    "delimited",
    "csv format",
    "flat file",
    
    # Header/trailer terms
    "header record",
    "detail record",
    "trailer record",
    "batch header",
    "batch trailer",
    "file header",
    "file trailer",
    
    # Validation terms
    "validation rules",
    "required field",
    "optional field",
    "mandatory",
    "max length",
    "min length",
    "format mask",
    "date format",
    
    # Common integration doc terms
    "integration guide",
    "technical specification",
    "data dictionary",
    "field reference",
    "schema definition",
}

# Patterns suggesting tabular field definitions
# These regex patterns help identify structured field definition sections
FIELD_TABLE_PATTERNS = [
    # Common header rows
    r"(?i)field\s*name.*(?:type|length|position|format)",
    r"(?i)column\s*name.*(?:type|length|position|format)",
    r"(?i)element.*(?:type|length|position|format)",
    
    # Position-based field definitions
    r"(?i)(?:position|pos)\s*[\d]+\s*[-–]\s*[\d]+",
    r"(?i)(?:byte|char)\s*[\d]+\s*[-–]\s*[\d]+",
    r"(?i)(?:start|from)\s*[\d]+.*(?:end|to|length)\s*[\d]+",
    
    # Length specifications
    r"(?i)(?:length|len|size)\s*[:=]?\s*[\d]+",
    r"(?i)[\d]+\s*(?:bytes?|chars?|characters?)",
    
    # Type specifications
    r"(?i)(?:type|format)\s*[:=]?\s*(?:string|numeric|date|decimal|integer|char|varchar)",
]

# Extensions that suggest PDF guide documents
PDF_EXTENSIONS: Set[str] = {
    ".pdf",
}

# MIME types for PDF
PDF_CONTENT_TYPES: Set[str] = {
    "application/pdf",
    "application/x-pdf",
}


# Default config singleton
_DEFAULT_CONFIG: PDFGuideConfidenceConfig = None


def get_config() -> PDFGuideConfidenceConfig:
    """
    Get the default PDF guide configuration.
    
    Returns cached singleton for efficiency.
    """
    global _DEFAULT_CONFIG
    if _DEFAULT_CONFIG is None:
        _DEFAULT_CONFIG = PDFGuideConfidenceConfig()
    return _DEFAULT_CONFIG


def reset_config() -> None:
    """Reset config cache (for testing)."""
    global _DEFAULT_CONFIG
    _DEFAULT_CONFIG = None


__all__ = [
    "PDFGuideConfidenceConfig",
    "GUIDE_KEYWORDS",
    "FIELD_TABLE_PATTERNS",
    "PDF_EXTENSIONS",
    "PDF_CONTENT_TYPES",
    "get_config",
    "reset_config",
]
