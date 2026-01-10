"""
Excel file detection and inference configuration.

Centralized thresholds and knobs for Excel handling.
All heuristics reference this config - no magic numbers elsewhere.

These values are tuned to:
- Minimize false positives (avoid claiming CSV/text files as Excel)
- Provide actionable confidence scores and warnings
- Enable explicit override via environment or programmatic config
"""

from dataclasses import dataclass, field
from typing import List, Optional
import os


@dataclass
class ExcelConfidenceConfig:
    """
    Configuration for Excel detection and inference confidence thresholds.
    
    All thresholds are floats in [0.0, 1.0] range unless otherwise noted.
    """
    
    # === Detection Thresholds ===
    # DETECT_MIN: Minimum confidence to accept as Excel
    # If confidence < DETECT_MIN, detection returns False (do not match)
    detect_min: float = 0.90
    
    # === Inference Thresholds ===
    # INFER_MIN: Minimum confidence to produce valid ParsedSpec
    # If confidence < INFER_MIN, return invalid ParsedSpec with errors
    infer_min: float = 0.50
    
    # INFER_WARN: Threshold below which warnings are emitted
    # If INFER_MIN <= confidence < INFER_WARN, proceed but emit warnings
    infer_warn: float = 0.75
    
    # === Sampling Parameters ===
    # Number of rows to sample for type inference (max)
    infer_nrows: int = 100
    
    # Minimum rows required for confident inference
    min_rows: int = 2
    
    # Maximum columns to infer (prevents runaway on noisy data)
    max_columns: int = 500
    
    # Maximum sheets to process (prevents runaway on large workbooks)
    max_sheets: int = 50
    
    # === Signal Weights for Detection ===
    # These weights are used to compute overall detection confidence
    # from individual signals. Must sum to ~1.0 for interpretability.
    # Note: content_type is optional (often not provided), so its weight
    # is redistributed when content_type=0 to avoid penalizing valid files.
    weight_zip_signature: float = 0.45  # Valid ZIP (XLSX) or OLE2 (XLS)
    weight_content_type: float = 0.10  # MIME type match (optional bonus)
    weight_extension_hint: float = 0.30  # .xlsx/.xls extension
    weight_workbook_load: float = 0.15  # openpyxl/xlrd load success
    
    # === Header Detection ===
    # If row 0 has > this ratio of string values vs numeric, treat as header
    header_string_ratio: float = 0.60
    
    # === Remediation Messages ===
    remediation_specify_header: str = (
        "Could not detect header row automatically. "
        "Use has_header=True/False configuration or provide column names via sidecar YAML."
    )
    remediation_empty_sheet: str = (
        "Sheet appears empty or contains no usable data. "
        "Verify the sheet name and check for hidden rows/columns."
    )
    remediation_too_many_columns: str = (
        "Sheet has more columns than max_columns limit. "
        "Increase max_columns in config or specify column range to parse."
    )
    remediation_corrupted: str = (
        "File appears to be a corrupted or invalid Excel file. "
        "Re-download or recreate the file, or check if it's actually CSV/text renamed to .xlsx."
    )
    
    @classmethod
    def from_env(cls) -> "ExcelConfidenceConfig":
        """
        Load config from environment variables.
        
        Environment variables (all optional):
        - EXCEL_DETECT_MIN
        - EXCEL_INFER_MIN
        - EXCEL_INFER_WARN
        - EXCEL_INFER_NROWS
        - EXCEL_MIN_ROWS
        - EXCEL_MAX_COLUMNS
        - EXCEL_MAX_SHEETS
        """
        def _get_float(key: str, default: float) -> float:
            val = os.environ.get(key)
            if val is None:
                return default
            try:
                return float(val)
            except ValueError:
                return default
        
        def _get_int(key: str, default: int) -> int:
            val = os.environ.get(key)
            if val is None:
                return default
            try:
                return int(val)
            except ValueError:
                return default
        
        return cls(
            detect_min=_get_float("EXCEL_DETECT_MIN", cls.detect_min),
            infer_min=_get_float("EXCEL_INFER_MIN", cls.infer_min),
            infer_warn=_get_float("EXCEL_INFER_WARN", cls.infer_warn),
            infer_nrows=_get_int("EXCEL_INFER_NROWS", cls.infer_nrows),
            min_rows=_get_int("EXCEL_MIN_ROWS", cls.min_rows),
            max_columns=_get_int("EXCEL_MAX_COLUMNS", cls.max_columns),
            max_sheets=_get_int("EXCEL_MAX_SHEETS", cls.max_sheets),
        )


@dataclass
class ExcelDetectionResult:
    """
    Rich detection result with confidence breakdown.
    
    Used internally by detect_with_confidence() and exposed
    in ParsedSpec metadata for debugging/auditing.
    """
    matched: bool
    confidence: float
    reasons: List[str] = field(default_factory=list)
    signals: dict = field(default_factory=dict)  # Individual signal scores
    
    def __str__(self) -> str:
        status = "MATCH" if self.matched else "NO_MATCH"
        return f"{status} (confidence={self.confidence:.2f}): {', '.join(self.reasons)}"


@dataclass
class ExcelInferenceResult:
    """
    Rich inference result with confidence and warnings.
    
    Returned by infer_schema() to separate schema from confidence/warnings.
    """
    sheets: Optional[List["ExcelSheetSchema"]] = None
    confidence: float = 0.0
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    
    def is_valid(self) -> bool:
        """Check if inference succeeded (has schema and no errors)."""
        return self.sheets is not None and len(self.sheets) > 0 and not self.errors
    
    def should_warn(self, config: ExcelConfidenceConfig) -> bool:
        """Check if warnings should be emitted based on config."""
        return self.confidence < config.infer_warn
    
    def should_reject(self, config: ExcelConfidenceConfig) -> bool:
        """Check if result should be rejected based on config."""
        return self.confidence < config.infer_min


@dataclass
class ExcelSheetSchema:
    """
    Inferred schema for a single Excel sheet.
    """
    sheet_name: str
    has_header: bool
    columns: List["ExcelColumnSchema"]
    row_count: int  # Total rows (including header)
    confidence: float = 1.0
    warnings: List[str] = field(default_factory=list)


@dataclass
class ExcelColumnSchema:
    """
    Inferred schema for a single Excel column.
    """
    name: str
    position: int  # 0-indexed column position
    inferred_type: str  # "string", "integer", "decimal", "date", "datetime", "boolean"
    nullable: bool = True
    sample_values: List[str] = field(default_factory=list)
    inference_confidence: float = 1.0


# Default global config (can be overridden)
DEFAULT_CONFIG = ExcelConfidenceConfig()


def get_config() -> ExcelConfidenceConfig:
    """Get the current configuration (from env or defaults)."""
    return ExcelConfidenceConfig.from_env()


__all__ = [
    "ExcelConfidenceConfig",
    "ExcelDetectionResult",
    "ExcelInferenceResult",
    "ExcelSheetSchema",
    "ExcelColumnSchema",
    "DEFAULT_CONFIG",
    "get_config",
]
