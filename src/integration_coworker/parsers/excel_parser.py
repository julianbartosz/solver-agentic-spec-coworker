"""
Excel file parser with confidence-gated detection.

Implements detection and schema inference for Excel files (XLSX/XLS) with:
- Rich confidence scoring (no silent heuristics)
- Explicit warnings propagated to ParsedSpec
- Per-sheet schema inference (one FileSpec per sheet)
- Production-safe detection that won't claim CSV/text as Excel

Detection uses weighted signals:
- ZIP/OLE2 signature validation
- Content-type hints
- Extension hints
- openpyxl/xlrd load success

All thresholds centralized in excel_config.py
"""

import logging
import io
import re
from typing import Any, Dict, List, Optional, Tuple, Union
from datetime import datetime, date

from .excel_config import (
    ExcelConfidenceConfig,
    ExcelDetectionResult,
    ExcelInferenceResult,
    ExcelSheetSchema,
    ExcelColumnSchema,
    get_config,
)

logger = logging.getLogger(__name__)


# ZIP file signature (PK..)
ZIP_SIGNATURE = b"PK\x03\x04"

# OLE2 compound document signature (for .xls files)
OLE2_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# Excel-related content types
EXCEL_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    "application/vnd.ms-excel",  # .xls
    "application/x-excel",
    "application/x-msexcel",
}

# Strong Excel extensions
EXCEL_EXTENSIONS = {".xlsx", ".xls", ".xlsm", ".xlsb"}


def detect_with_confidence(
    content: Union[bytes, str],
    uri: str,
    config: Optional[ExcelConfidenceConfig] = None,
) -> ExcelDetectionResult:
    """
    Detect if content is an Excel file with confidence scoring.
    
    Computes weighted signals:
    - zip_signature (40%): Valid ZIP for XLSX or OLE2 for XLS
    - content_type (20%): MIME type match
    - extension_hint (25%): .xlsx/.xls extension
    - workbook_load (15%): Successful openpyxl/xlrd load
    
    Args:
        content: File content (bytes preferred, str will be encoded)
        uri: Source URI for extension detection
        config: Optional config (uses default if not provided)
        
    Returns:
        ExcelDetectionResult with matched, confidence, reasons, and signals
    """
    config = config or get_config()
    
    # Ensure bytes
    if isinstance(content, str):
        content_bytes = content.encode('latin-1', errors='ignore')
    else:
        content_bytes = content
    
    signals: Dict[str, float] = {}
    reasons: List[str] = []
    hard_reject = False
    
    # === Signal 1: File signature (ZIP for XLSX, OLE2 for XLS) ===
    if content_bytes[:4] == ZIP_SIGNATURE:
        signals["zip_signature"] = 1.0
        reasons.append("zip_signature=1.00 (valid ZIP/XLSX header)")
    elif content_bytes[:8] == OLE2_SIGNATURE:
        signals["zip_signature"] = 0.9
        reasons.append("zip_signature=0.90 (OLE2/XLS header)")
    elif _looks_like_text(content_bytes[:1000]):
        signals["zip_signature"] = 0.0
        reasons.append("zip_signature=0.00 (looks like text, not binary)")
        hard_reject = True
    else:
        signals["zip_signature"] = 0.0
        reasons.append("zip_signature=0.00 (unknown binary format)")
    
    # === Signal 2: Content type hint ===
    # Content type is not passed here, but we check uri for common patterns
    # This signal is populated if caller provides content_type
    signals["content_type"] = 0.0  # Default - will be overridden by source
    
    # === Signal 3: Extension hint ===
    uri_lower = uri.lower()
    if any(uri_lower.endswith(ext) for ext in EXCEL_EXTENSIONS):
        signals["extension_hint"] = 1.0
        reasons.append("extension_hint=1.00 (explicit Excel extension)")
    elif any(uri_lower.endswith(ext) for ext in [".csv", ".tsv", ".txt", ".json"]):
        signals["extension_hint"] = 0.0
        reasons.append("extension_hint=0.00 (text file extension)")
        hard_reject = True
    else:
        signals["extension_hint"] = 0.3
        reasons.append("extension_hint=0.30 (unknown extension)")
    
    # === Signal 4: Workbook load test ===
    workbook_score, workbook_reason = _try_load_workbook(content_bytes)
    signals["workbook_load"] = workbook_score
    reasons.append(f"workbook_load={workbook_score:.2f} ({workbook_reason})")
    
    if workbook_score == 0.0 and signals["zip_signature"] > 0:
        # Has ZIP signature but failed to load - might be a zip file, not Excel
        hard_reject = True
    
    # === Compute weighted confidence ===
    confidence = (
        signals["zip_signature"] * config.weight_zip_signature +
        signals["content_type"] * config.weight_content_type +
        signals["extension_hint"] * config.weight_extension_hint +
        signals["workbook_load"] * config.weight_workbook_load
    )
    
    # Hard rejects always return low confidence
    if hard_reject:
        confidence = min(confidence, 0.20)
    
    # Determine match
    matched = confidence >= config.detect_min and not hard_reject
    
    return ExcelDetectionResult(
        matched=matched,
        confidence=confidence,
        reasons=reasons,
        signals=signals,
    )


def _looks_like_text(sample: bytes) -> bool:
    """Check if content appears to be text rather than binary."""
    if len(sample) == 0:
        return False
    
    # Check for high ratio of printable ASCII characters
    printable_count = sum(
        1 for b in sample
        if 0x20 <= b <= 0x7e or b in (0x09, 0x0a, 0x0d)  # Space-~ or tab/newline
    )
    ratio = printable_count / len(sample)
    return ratio > 0.85


def _try_load_workbook(content_bytes: bytes) -> Tuple[float, str]:
    """
    Try to load content as Excel workbook.
    
    Returns (score, reason) where score is 0-1.
    """
    try:
        import openpyxl
        
        workbook = openpyxl.load_workbook(
            io.BytesIO(content_bytes),
            read_only=True,
            data_only=True,
        )
        
        sheet_count = len(workbook.sheetnames)
        workbook.close()
        
        if sheet_count > 0:
            return 1.0, f"openpyxl loaded {sheet_count} sheet(s)"
        else:
            return 0.5, "openpyxl loaded but no sheets found"
            
    except ImportError:
        return 0.5, "openpyxl not installed"
    except Exception as e:
        error_str = str(e).lower()
        if "zipfile" in error_str or "not a zip" in error_str:
            return 0.0, "not a valid ZIP/XLSX file"
        elif "corrupt" in error_str or "invalid" in error_str:
            return 0.0, f"corrupted or invalid Excel file"
        else:
            return 0.0, f"openpyxl load failed: {str(e)[:50]}"


def infer_schema(
    content: Union[bytes, str],
    config: Optional[ExcelConfidenceConfig] = None,
) -> ExcelInferenceResult:
    """
    Infer schema from Excel workbook content.
    
    Creates one ExcelSheetSchema per sheet with:
    - Header detection (heuristic: first row string-dominant)
    - Column type inference (string, integer, decimal, date, datetime, boolean)
    - Sample values for each column
    - Formula cell detection with warnings (per TD-XLS-001)
    
    Args:
        content: Excel file content (bytes preferred)
        config: Optional config for thresholds
        
    Returns:
        ExcelInferenceResult with sheets, confidence, warnings, errors
    """
    config = config or get_config()
    
    # Ensure bytes
    if isinstance(content, str):
        content_bytes = content.encode('latin-1', errors='ignore')
    else:
        content_bytes = content
    
    try:
        import openpyxl
    except ImportError:
        return ExcelInferenceResult(
            sheets=None,
            confidence=0.0,
            errors=["openpyxl not installed. Install with: pip install openpyxl"],
        )
    
    try:
        workbook = openpyxl.load_workbook(
            io.BytesIO(content_bytes),
            read_only=True,
            data_only=True,  # Get computed values, not formulas
        )
    except Exception as e:
        return ExcelInferenceResult(
            sheets=None,
            confidence=0.0,
            errors=[f"Failed to load Excel workbook: {e}. {config.remediation_corrupted}"],
        )
    
    sheets: List[ExcelSheetSchema] = []
    all_warnings: List[str] = []
    all_errors: List[str] = []
    confidences: List[float] = []
    
    try:
        sheet_names = workbook.sheetnames[:config.max_sheets]
        
        if len(workbook.sheetnames) > config.max_sheets:
            all_warnings.append(
                f"Workbook has {len(workbook.sheetnames)} sheets, "
                f"processing only first {config.max_sheets}."
            )
        
        for sheet_name in sheet_names:
            sheet = workbook[sheet_name]
            
            sheet_schema, sheet_confidence, sheet_warnings, sheet_errors = _infer_sheet_schema(
                sheet,
                sheet_name,
                config,
            )
            
            if sheet_schema:
                sheets.append(sheet_schema)
                confidences.append(sheet_confidence)
            
            all_warnings.extend(sheet_warnings)
            all_errors.extend(sheet_errors)
    
    finally:
        workbook.close()
    
    # === Formula Detection (TD-XLS-001) ===
    # For small files, check for formula cells and warn users
    # This requires a second pass with data_only=False
    formula_warnings = _detect_formula_cells(content_bytes, config)
    all_warnings.extend(formula_warnings)
    
    if not sheets:
        return ExcelInferenceResult(
            sheets=None,
            confidence=0.0,
            errors=all_errors if all_errors else [config.remediation_empty_sheet],
            warnings=all_warnings,
        )
    
    # Overall confidence is average of per-sheet confidences
    overall_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    
    # Apply minimum threshold
    if overall_confidence < config.infer_min:
        all_errors.append(
            f"Inference confidence too low ({overall_confidence:.2f} < {config.infer_min}). "
            f"Consider providing explicit column names via sidecar configuration."
        )
        return ExcelInferenceResult(
            sheets=None,
            confidence=overall_confidence,
            errors=all_errors,
            warnings=all_warnings,
        )
    
    # Add warning if below warn threshold
    if overall_confidence < config.infer_warn:
        all_warnings.append(
            f"Low inference confidence ({overall_confidence:.2f} < {config.infer_warn}). "
            f"Results may be inaccurate. Consider providing explicit column names."
        )
    
    return ExcelInferenceResult(
        sheets=sheets,
        confidence=overall_confidence,
        warnings=all_warnings,
        errors=[],  # Clear errors if we have valid result
    )


def _detect_formula_cells(
    content_bytes: bytes,
    config: ExcelConfidenceConfig,
) -> List[str]:
    """
    Detect formula cells in Excel workbook.
    
    For files < 10MB, loads workbook with data_only=False to detect
    formula cells and emit warnings about potentially stale values.
    
    Per docs/BUCKET_2_TECH_DEBT_AND_SCALING.md TD-XLS-001
    
    Args:
        content_bytes: Excel file content
        config: Config with infer_nrows limit
        
    Returns:
        List of warning messages about formula cells
    """
    # Skip formula detection for large files (memory safety)
    MAX_SIZE_FOR_FORMULA_CHECK = 10 * 1024 * 1024  # 10MB
    if len(content_bytes) > MAX_SIZE_FOR_FORMULA_CHECK:
        return []
    
    warnings = []
    
    try:
        import openpyxl
        
        # Load with data_only=False to see formulas
        formula_workbook = openpyxl.load_workbook(
            io.BytesIO(content_bytes),
            read_only=False,  # Required for formula detection
            data_only=False,
        )
        
        try:
            for sheet_name in formula_workbook.sheetnames[:config.max_sheets]:
                sheet = formula_workbook[sheet_name]
                formula_cells = []
                
                # Scan first N rows for formulas
                for row_idx, row in enumerate(sheet.iter_rows(max_row=config.infer_nrows), start=1):
                    for cell in row:
                        # Check if cell has a formula
                        if cell.data_type == 'f' or (
                            isinstance(cell.value, str) and 
                            cell.value and 
                            cell.value.startswith('=')
                        ):
                            formula_cells.append(cell.coordinate)
                
                if formula_cells:
                    # Limit examples to first 5 cells
                    examples = ', '.join(formula_cells[:5])
                    extra = f" and {len(formula_cells) - 5} more" if len(formula_cells) > 5 else ""
                    warnings.append(
                        f"Sheet '{sheet_name}' contains {len(formula_cells)} formula cell(s) "
                        f"(e.g., {examples}{extra}). Values shown are cached and may be stale "
                        f"if workbook was not saved with Excel or if it references external data."
                    )
        finally:
            formula_workbook.close()
            
    except Exception as e:
        # Don't fail the main inference if formula detection fails
        logger.debug(f"Could not check for formulas: {e}")
    
    return warnings


def _infer_sheet_schema(
    sheet,
    sheet_name: str,
    config: ExcelConfidenceConfig,
) -> Tuple[Optional[ExcelSheetSchema], float, List[str], List[str]]:
    """
    Infer schema for a single sheet.
    
    Returns (schema, confidence, warnings, errors).
    """
    warnings: List[str] = []
    errors: List[str] = []
    
    # Read sample rows
    rows = []
    for i, row in enumerate(sheet.iter_rows(values_only=True)):
        if i >= config.infer_nrows:
            break
        rows.append(row)
    
    if len(rows) < config.min_rows:
        return None, 0.0, warnings, [
            f"Sheet '{sheet_name}' has only {len(rows)} row(s), "
            f"need at least {config.min_rows}. {config.remediation_empty_sheet}"
        ]
    
    # Determine number of columns
    max_cols = max(len(row) for row in rows) if rows else 0
    
    if max_cols == 0:
        return None, 0.0, warnings, [
            f"Sheet '{sheet_name}' appears to be empty. {config.remediation_empty_sheet}"
        ]
    
    if max_cols > config.max_columns:
        warnings.append(
            f"Sheet '{sheet_name}' has {max_cols} columns, "
            f"truncating to {config.max_columns}. {config.remediation_too_many_columns}"
        )
        max_cols = config.max_columns
    
    # Detect header row
    first_row = rows[0] if rows else []
    has_header, header_confidence = _detect_header_row(first_row, rows[1:] if len(rows) > 1 else [])
    
    if header_confidence < 0.6:
        warnings.append(
            f"Sheet '{sheet_name}': {config.remediation_specify_header}"
        )
    
    # Extract column names
    if has_header:
        column_names = [
            _safe_column_name(first_row[i] if i < len(first_row) else None, i)
            for i in range(max_cols)
        ]
        data_rows = rows[1:]
    else:
        column_names = [f"column_{i}" for i in range(max_cols)]
        data_rows = rows
    
    # Infer column types
    columns: List[ExcelColumnSchema] = []
    column_confidences: List[float] = []
    
    for col_idx in range(max_cols):
        col_values = [
            row[col_idx] if col_idx < len(row) else None
            for row in data_rows
        ]
        
        col_schema, col_confidence = _infer_column_schema(
            column_names[col_idx],
            col_idx,
            col_values,
        )
        
        columns.append(col_schema)
        column_confidences.append(col_confidence)
    
    # Sheet confidence is combination of header detection and column inference
    avg_col_confidence = sum(column_confidences) / len(column_confidences) if column_confidences else 0.5
    sheet_confidence = 0.3 * header_confidence + 0.7 * avg_col_confidence
    
    schema = ExcelSheetSchema(
        sheet_name=sheet_name,
        has_header=has_header,
        columns=columns,
        row_count=len(rows),
        confidence=sheet_confidence,
        warnings=warnings,
    )
    
    return schema, sheet_confidence, warnings, errors


def _detect_header_row(
    first_row: Tuple,
    remaining_rows: List[Tuple],
) -> Tuple[bool, float]:
    """
    Detect if first row is a header row.
    
    Heuristics:
    - Header rows have high string ratio
    - Header rows have unique values
    - Header rows don't match pattern of data rows
    
    Returns (has_header, confidence).
    """
    if not first_row:
        return False, 0.5
    
    # Count string values in first row
    non_null = [v for v in first_row if v is not None]
    if not non_null:
        return False, 0.5
    
    string_count = sum(1 for v in non_null if isinstance(v, str) and not _looks_like_number(v))
    string_ratio = string_count / len(non_null)
    
    # High string ratio suggests header
    if string_ratio > 0.7:
        return True, min(0.95, 0.5 + string_ratio * 0.5)
    
    # Low string ratio suggests not header
    if string_ratio < 0.3:
        return False, min(0.95, 0.5 + (1 - string_ratio) * 0.5)
    
    # Ambiguous - check if values look like column names
    looks_like_names = sum(
        1 for v in non_null
        if isinstance(v, str) and _looks_like_column_name(v)
    )
    name_ratio = looks_like_names / len(non_null)
    
    if name_ratio > 0.5:
        return True, 0.7
    
    return False, 0.5


def _looks_like_number(value: Any) -> bool:
    """Check if string value looks like a number."""
    if not isinstance(value, str):
        return isinstance(value, (int, float))
    
    try:
        float(value.replace(",", ""))
        return True
    except (ValueError, AttributeError):
        return False


def _looks_like_column_name(value: str) -> bool:
    """Check if value looks like a column name."""
    if not isinstance(value, str):
        return False
    
    # Column names are typically short, alphanumeric with underscores/spaces
    if len(value) > 50:
        return False
    
    # Contains only letters, numbers, spaces, underscores
    if re.match(r'^[a-zA-Z][a-zA-Z0-9_\s]*$', value):
        return True
    
    return False


def _safe_column_name(value: Any, position: int) -> str:
    """Convert cell value to safe column name."""
    if value is None:
        return f"column_{position}"
    
    name = str(value).strip()
    
    if not name:
        return f"column_{position}"
    
    # Clean up name
    name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    name = re.sub(r'_+', '_', name)
    name = name.strip('_').lower()
    
    if not name or name[0].isdigit():
        name = f"col_{name}" if name else f"column_{position}"
    
    return name


def _infer_column_schema(
    name: str,
    position: int,
    values: List[Any],
) -> Tuple[ExcelColumnSchema, float]:
    """
    Infer schema for a single column.
    
    Returns (schema, confidence).
    """
    non_null = [v for v in values if v is not None and v != ""]
    
    if not non_null:
        # All null - unknown type, low confidence
        return ExcelColumnSchema(
            name=name,
            position=position,
            inferred_type="string",
            nullable=True,
            sample_values=[],
            inference_confidence=0.3,
        ), 0.3
    
    nullable = len(non_null) < len(values)
    
    # Sample values for reference
    sample_values = [str(v)[:100] for v in non_null[:5]]
    
    # Infer type from values
    inferred_type, type_confidence = _infer_type_from_values(non_null)
    
    return ExcelColumnSchema(
        name=name,
        position=position,
        inferred_type=inferred_type,
        nullable=nullable,
        sample_values=sample_values,
        inference_confidence=type_confidence,
    ), type_confidence


def _infer_type_from_values(values: List[Any]) -> Tuple[str, float]:
    """
    Infer data type from list of values.
    
    Returns (type_name, confidence).
    """
    if not values:
        return "string", 0.5
    
    # Count types
    type_counts = {
        "integer": 0,
        "decimal": 0,
        "date": 0,
        "datetime": 0,
        "boolean": 0,
        "string": 0,
    }
    
    for v in values:
        detected = _detect_value_type(v)
        type_counts[detected] += 1
    
    # Find dominant type
    total = len(values)
    best_type = "string"
    best_count = 0
    
    for t, count in type_counts.items():
        if count > best_count:
            best_count = count
            best_type = t
    
    # Confidence is ratio of dominant type
    confidence = best_count / total
    
    # Boost confidence for homogeneous columns
    if confidence > 0.9:
        confidence = min(1.0, confidence + 0.05)
    
    return best_type, confidence


def _detect_value_type(value: Any) -> str:
    """Detect type of a single value."""
    if value is None:
        return "string"
    
    if isinstance(value, bool):
        return "boolean"
    
    if isinstance(value, datetime):
        if value.hour == 0 and value.minute == 0 and value.second == 0:
            return "date"
        return "datetime"
    
    if isinstance(value, date):
        return "date"
    
    if isinstance(value, int):
        return "integer"
    
    if isinstance(value, float):
        if value == int(value):
            return "integer"
        return "decimal"
    
    if isinstance(value, str):
        value_str = value.strip()
        
        # Boolean
        if value_str.lower() in ("true", "false", "yes", "no", "y", "n", "1", "0"):
            return "boolean"
        
        # Integer
        try:
            int(value_str.replace(",", ""))
            return "integer"
        except ValueError:
            pass
        
        # Decimal
        try:
            float(value_str.replace(",", ""))
            return "decimal"
        except ValueError:
            pass
        
        # Date patterns
        date_patterns = [
            r'^\d{4}-\d{2}-\d{2}$',  # ISO date
            r'^\d{2}/\d{2}/\d{4}$',  # US date
            r'^\d{2}-\d{2}-\d{4}$',  # EU date
        ]
        for pattern in date_patterns:
            if re.match(pattern, value_str):
                return "date"
        
        # Datetime patterns
        datetime_patterns = [
            r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}',  # ISO datetime
        ]
        for pattern in datetime_patterns:
            if re.match(pattern, value_str):
                return "datetime"
    
    return "string"


__all__ = [
    "detect_with_confidence",
    "infer_schema",
    "ExcelDetectionResult",
    "ExcelInferenceResult",
    "ExcelSheetSchema",
    "ExcelColumnSchema",
]
