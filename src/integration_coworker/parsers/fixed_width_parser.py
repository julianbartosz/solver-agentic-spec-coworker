"""
Fixed-width file parser with confidence scoring.

Provides deterministic parsing of fixed-width files with explicit
confidence thresholds and warnings. No silent heuristics.

Key APIs:
- infer_schema(lines, config) -> InferenceResult (schema + confidence + warnings)
- parse_with_schema(lines, schema) -> List[Dict] (rows)
- detect_with_confidence(content, uri, config) -> DetectionResult

Design principles:
- No guessing: If confidence < threshold, return errors with remediation
- Explicit: Every heuristic produces a score and reason
- Testable: Confidence breakdown exposed for assertions
- Safe: Reject ambiguous content rather than produce wrong results
"""

import re
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

from .fixed_width_config import (
    FixedWidthConfidenceConfig,
    DetectionResult,
    InferenceResult,
    get_config,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class FixedWidthField:
    """A field in a fixed-width file."""
    name: str
    start: int  # 0-indexed start position
    length: int
    inferred_type: str = "string"  # string, integer, decimal, date, etc.
    nullable: bool = True
    sample_values: List[str] = field(default_factory=list)
    inference_confidence: float = 0.8
    
    @property
    def end(self) -> int:
        """End position (exclusive)."""
        return self.start + self.length


@dataclass
class FixedWidthSchema:
    """Schema for a fixed-width file."""
    line_length: int
    fields: List[FixedWidthField]
    has_header: bool = False
    encoding: str = "utf-8"
    line_terminator: str = "\n"
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    record_type: str = "detail"  # header, detail, trailer for multi-record
    record_type_position: Optional[Tuple[int, int]] = None  # (start, length) for type discriminator
    
    @property
    def total_field_length(self) -> int:
        """Sum of all field lengths."""
        return sum(f.length for f in self.fields)
    
    def is_valid(self) -> bool:
        """Check if schema is valid (has fields and no errors)."""
        return len(self.fields) > 0 and not self.errors
    
    def validate_coverage(self) -> List[str]:
        """Check for gaps/overlaps in field positions."""
        if not self.fields:
            return ["No fields defined"]
        
        errors = []
        sorted_fields = sorted(self.fields, key=lambda f: f.start)
        
        # Check for overlap/gaps
        for i, f in enumerate(sorted_fields):
            if i == 0:
                if f.start > 0:
                    errors.append(f"Gap at start: positions 0-{f.start - 1} uncovered")
            else:
                prev = sorted_fields[i - 1]
                if f.start < prev.end:
                    errors.append(
                        f"Overlap: {prev.name} (ends at {prev.end}) "
                        f"overlaps {f.name} (starts at {f.start})"
                    )
                elif f.start > prev.end:
                    errors.append(
                        f"Gap: positions {prev.end}-{f.start - 1} "
                        f"uncovered between {prev.name} and {f.name}"
                    )
        
        # Check total coverage matches line length
        if sorted_fields:
            last = sorted_fields[-1]
            if last.end < self.line_length:
                errors.append(
                    f"Uncovered end: positions {last.end}-{self.line_length - 1}"
                )
            elif last.end > self.line_length:
                errors.append(
                    f"Fields exceed line length: {last.end} > {self.line_length}"
                )
        
        return errors


# =============================================================================
# Detection (with rich confidence)
# =============================================================================

def detect_with_confidence(
    content: str,
    uri: str = "",
    config: Optional[FixedWidthConfidenceConfig] = None,
) -> DetectionResult:
    """
    Detect if content is fixed-width with rich confidence breakdown.
    
    Returns DetectionResult with:
    - matched: True if confidence >= config.detect_min
    - confidence: Weighted score from individual signals
    - reasons: Human-readable explanation of each signal
    - signals: Dict of signal_name -> score for testing/auditing
    
    Detection signals (weighted):
    1. Line length consistency (primary)
    2. Delimiter absence (reject if delimited)
    3. Boundary stability (whitespace runs at stable positions)
    4. Extension hint (secondary)
    
    Hard rejects (confidence=0):
    - Consistent delimiter patterns (CSV/TSV/pipe)
    - Space-delimited table patterns
    - Too few lines for analysis
    
    IMPORTANT: Even on hard reject, ALL signals are computed and returned
    for debugging and explainability (ops safety).
    """
    config = config or get_config()
    
    lines = content.split('\n')
    non_empty = [line.rstrip('\r') for line in lines if line.strip()]
    
    signals = {}
    reasons = []
    hard_reject_reason = None  # Track the reason for hard rejection
    
    # Hard reject: too few lines
    if len(non_empty) < config.min_rows:
        signals["line_count"] = len(non_empty)
        hard_reject_reason = f"Too few lines ({len(non_empty)} < {config.min_rows})"
        reasons.append(hard_reject_reason)
        
        # Still return partial signals for debugging
        return DetectionResult(
            matched=False,
            confidence=0.0,
            reasons=reasons,
            signals=signals,
        )
    
    # Sample lines for analysis (limit to infer_nrows)
    sample_lines = non_empty[:config.infer_nrows]
    
    # === COMPUTE ALL SIGNALS FIRST (for explainability) ===
    
    # === Signal 1: Line length consistency ===
    line_consistency, consistency_reason = _compute_line_consistency(
        sample_lines, config
    )
    signals["line_consistency"] = line_consistency
    reasons.append(f"line_consistency={line_consistency:.2f} ({consistency_reason})")
    
    # Check for hard reject but don't return yet
    line_consistency_reject = line_consistency < (1.0 - config.line_length_variance_max)
    if line_consistency_reject:
        hard_reject_reason = f"Line lengths too variable: {consistency_reason}"
    
    # === Signal 2: Delimiter absence (hard reject if delimited) ===
    delimiter_absence, delimiter_reason = _compute_delimiter_absence(
        sample_lines, config
    )
    signals["delimiter_absence"] = delimiter_absence
    reasons.append(f"delimiter_absence={delimiter_absence:.2f} ({delimiter_reason})")
    
    # Check for hard reject but don't return yet
    delimiter_reject = delimiter_absence < 0.5  # Strong delimiter pattern
    if delimiter_reject and not hard_reject_reason:
        hard_reject_reason = f"Looks delimited: {delimiter_reason}"
    
    # === Signal 3: Boundary stability (whitespace runs) ===
    boundary_stability, boundary_reason = _compute_boundary_stability(
        sample_lines, config
    )
    signals["boundary_stability"] = boundary_stability
    reasons.append(f"boundary_stability={boundary_stability:.2f} ({boundary_reason})")
    
    # === Signal 4: Extension hint ===
    extension_score, ext_reason = _compute_extension_score(uri)
    signals["extension_hint"] = extension_score
    reasons.append(f"extension_hint={extension_score:.2f} ({ext_reason})")
    
    # === NOW apply hard rejects (after all signals computed) ===
    if hard_reject_reason:
        # Prepend the hard reject reason to make it prominent
        reasons.insert(0, f"HARD REJECT: {hard_reject_reason}")
        return DetectionResult(
            matched=False,
            confidence=0.0,
            reasons=reasons,
            signals=signals,  # All signals included for debugging
        )
    
    # === Compute weighted confidence ===
    confidence = (
        config.weight_line_consistency * line_consistency +
        config.weight_delimiter_absence * delimiter_absence +
        config.weight_boundary_stability * boundary_stability +
        config.weight_extension_hint * extension_score
    )
    
    # Clamp to [0, 1]
    confidence = max(0.0, min(1.0, confidence))
    
    matched = confidence >= config.detect_min
    
    return DetectionResult(
        matched=matched,
        confidence=confidence,
        reasons=reasons,
        signals=signals,
    )


def _compute_line_consistency(
    lines: List[str],
    config: FixedWidthConfidenceConfig,
) -> Tuple[float, str]:
    """
    Compute line length consistency score.
    
    Returns (score, reason) where score is in [0, 1].
    """
    if not lines:
        return 0.0, "no lines"
    
    lengths = [len(line) for line in lines]
    length_counts = Counter(lengths)
    
    # Most common length
    most_common_length, most_common_count = length_counts.most_common(1)[0]
    consistency_ratio = most_common_count / len(lengths)
    
    # Variance check
    unique_lengths = len(length_counts)
    
    if unique_lengths == 1:
        return 1.0, f"all lines {most_common_length} chars"
    elif unique_lengths <= 3 and consistency_ratio >= 0.9:
        return consistency_ratio, f"{consistency_ratio:.0%} lines at {most_common_length} chars"
    else:
        return consistency_ratio * 0.5, f"{unique_lengths} different lengths"


def _compute_delimiter_absence(
    lines: List[str],
    config: FixedWidthConfidenceConfig,
) -> Tuple[float, str]:
    """
    Compute delimiter absence score (high = no delimiters = good for fixed-width).
    
    Returns (score, reason) where score is in [0, 1].
    """
    delimiters = [',', '\t', '|', ';']
    
    for delim in delimiters:
        # Check if delimiter appears consistently
        delim_counts = [line.count(delim) for line in lines]
        
        # Consistent delimiter count suggests delimited format
        if len(set(delim_counts)) <= 2:  # Very consistent
            min_count = min(delim_counts)
            if min_count >= 2:  # At least 2 delimiters per line
                return 0.0, f"consistent '{_escape_delim(delim)}' delimiter ({min_count}+ per line)"
        
        # Check delimiter rate (positions with delimiter / total positions)
        total_delims = sum(delim_counts)
        total_chars = sum(len(line) for line in lines)
        if total_chars > 0:
            delim_rate = total_delims / total_chars
            if delim_rate > config.delimiter_rate_max:
                return 0.2, f"high '{_escape_delim(delim)}' rate ({delim_rate:.1%})"
    
    return 1.0, "no consistent delimiters"


def _escape_delim(delim: str) -> str:
    """Escape delimiter for display."""
    if delim == '\t':
        return '\\t'
    return delim


def _compute_boundary_stability(
    lines: List[str],
    config: FixedWidthConfidenceConfig,
) -> Tuple[float, str]:
    """
    Compute boundary stability score from whitespace run positions.
    
    Fixed-width files have whitespace padding at consistent positions.
    This measures how stable those positions are across lines.
    
    Returns (score, reason) where score is in [0, 1].
    """
    if not lines:
        return 0.0, "no lines"
    
    # Get the most common line length
    lengths = [len(line) for line in lines]
    line_length = Counter(lengths).most_common(1)[0][0]
    
    # Filter to lines with the target length
    target_lines = [line for line in lines if len(line) == line_length]
    if len(target_lines) < config.min_rows:
        return 0.5, f"too few lines at target length ({len(target_lines)})"
    
    # Find whitespace run boundaries across all lines
    boundary_counts = Counter()
    
    for line in target_lines:
        # Find positions where whitespace runs start (transition from non-space to space)
        for i in range(len(line) - 1):
            if line[i] != ' ' and line[i + 1] == ' ':
                boundary_counts[i + 1] += 1
            # Also mark where data resumes (transition from space to non-space)
            if line[i] == ' ' and line[i + 1] != ' ':
                boundary_counts[i + 1] += 1
    
    if not boundary_counts:
        # No whitespace runs - could be dense fixed-width or not fixed-width
        # Check for multi-space runs
        multi_space_lines = sum(1 for line in target_lines if '  ' in line)
        if multi_space_lines / len(target_lines) > 0.5:
            return 0.6, "whitespace present but no stable boundaries"
        return 0.3, "no whitespace runs detected"
    
    # Calculate support for each boundary
    total_lines = len(target_lines)
    high_support_boundaries = [
        pos for pos, count in boundary_counts.items()
        if count / total_lines >= config.boundary_support_min
    ]
    
    # Score based on number and consistency of boundaries
    if len(high_support_boundaries) >= 3:
        avg_support = sum(
            boundary_counts[pos] / total_lines
            for pos in high_support_boundaries
        ) / len(high_support_boundaries)
        return avg_support, f"{len(high_support_boundaries)} stable boundaries"
    elif len(high_support_boundaries) >= 1:
        return 0.6, f"only {len(high_support_boundaries)} stable boundary(s)"
    else:
        return 0.3, "no high-support boundaries"


def _compute_extension_score(uri: str) -> Tuple[float, str]:
    """
    Compute extension hint score.
    
    Returns (score, reason) where score is in [0, 1].
    """
    uri_lower = uri.lower()
    
    # Strong fixed-width hints
    if any(uri_lower.endswith(ext) for ext in ['.fw', '.fixed']):
        return 1.0, "explicit fixed-width extension"
    
    # Medium hints (common for fixed-width)
    if uri_lower.endswith('.dat'):
        return 0.8, ".dat extension (often fixed-width)"
    
    # Weak hints (ambiguous)
    if uri_lower.endswith('.txt'):
        return 0.5, ".txt extension (ambiguous)"
    
    # Negative hints (clearly not fixed-width)
    if any(uri_lower.endswith(ext) for ext in ['.csv', '.tsv', '.json', '.xml', '.yaml', '.yml']):
        return 0.0, "incompatible extension"
    
    # No hint
    return 0.3, "no extension hint"


# =============================================================================
# Schema Inference (with rich confidence)
# =============================================================================

def infer_schema(
    content: str,
    *,
    config: Optional[FixedWidthConfidenceConfig] = None,
    require_full_coverage: bool = True,
) -> InferenceResult:
    """
    Infer fixed-width schema with explicit confidence scoring.
    
    Returns InferenceResult with:
    - schema: FixedWidthSchema if successful, None if failed
    - confidence: Overall inference confidence [0, 1]
    - warnings: List of warnings (non-fatal issues)
    - errors: List of errors (fatal issues)
    - boundary_supports: Dict of position -> support score for auditing
    
    Confidence is computed from:
    - Boundary support scores (how consistent are column boundaries)
    - Coverage factor (do fields span the full line)
    - Field count reasonableness (not too few, not too many)
    
    If confidence < config.infer_min, returns errors with remediation.
    If confidence < config.infer_warn, returns warnings with schema.
    """
    config = config or get_config()
    
    lines = content.split('\n')
    non_empty = [line.rstrip('\r') for line in lines if line.strip()]
    
    # Limit to infer_nrows
    sample_lines = non_empty[:config.infer_nrows]
    
    # Step 1: Determine line length
    line_length = _infer_line_length(sample_lines, config)
    
    if line_length is None:
        return InferenceResult(
            schema=None,
            confidence=0.0,
            errors=[
                "Cannot infer fixed-width schema: line lengths are inconsistent. "
                f"{config.remediation_explicit_colspec}"
            ],
        )
    
    # Filter to target-length lines
    target_lines = [line for line in sample_lines if len(line) == line_length]
    
    if len(target_lines) < config.min_rows:
        return InferenceResult(
            schema=None,
            confidence=0.0,
            errors=[
                f"Too few lines at target length {line_length}: "
                f"{len(target_lines)} < {config.min_rows}. "
                f"{config.remediation_increase_samples}"
            ],
        )
    
    # Step 2: Infer column boundaries with support scores
    colspec, boundary_supports, boundary_confidence = _infer_colspec_with_support(
        target_lines, line_length, config
    )
    
    if colspec is None:
        return InferenceResult(
            schema=None,
            confidence=0.0,
            errors=[
                "Cannot infer column boundaries: no stable whitespace patterns. "
                f"{config.remediation_explicit_colspec}"
            ],
            boundary_supports=boundary_supports,
        )
    
    # Step 3: Check column count
    if len(colspec) > config.max_columns:
        return InferenceResult(
            schema=None,
            confidence=0.0,
            errors=[
                f"Too many columns inferred ({len(colspec)} > {config.max_columns}). "
                "This may not be a fixed-width file or the boundaries are wrong. "
                f"{config.remediation_explicit_colspec}"
            ],
            boundary_supports=boundary_supports,
        )
    
    # Step 4: Detect field types
    types = _detect_field_types(target_lines, colspec, line_length)
    
    # Step 5: Build fields
    fields = []
    for i, ((start, length), ftype) in enumerate(zip(colspec, types)):
        samples = _extract_samples(target_lines, start, length, max_samples=5)
        fields.append(FixedWidthField(
            name=f"field_{i}",
            start=start,
            length=length,
            inferred_type=ftype,
            sample_values=samples,
            inference_confidence=boundary_supports.get(start, 0.8),
        ))
    
    # Step 6: Build schema
    schema = FixedWidthSchema(
        line_length=line_length,
        fields=fields,
        has_header=False,  # Conservative default
    )
    
    # Step 7: Validate coverage
    coverage_errors = schema.validate_coverage()
    coverage_factor = 1.0
    if coverage_errors and require_full_coverage:
        coverage_factor = 0.7  # Penalty for incomplete coverage
    
    # Step 8: Compute overall confidence
    # Confidence = mean(boundary_supports) * coverage_factor * field_count_factor
    if boundary_supports:
        mean_support = sum(boundary_supports.values()) / len(boundary_supports)
    else:
        mean_support = 0.5
    
    # Penalize extreme field counts
    field_count_factor = 1.0
    if len(fields) < 2:
        field_count_factor = 0.5
    elif len(fields) > 50:
        field_count_factor = 0.8
    
    confidence = mean_support * coverage_factor * field_count_factor * boundary_confidence
    confidence = max(0.0, min(1.0, confidence))
    
    # Step 9: Generate warnings
    warnings = []
    
    if confidence < config.infer_warn:
        warnings.append(
            f"Low inference confidence ({confidence:.2f} < {config.infer_warn}). "
            "Results may be inaccurate. Consider providing explicit colspec."
        )
    
    if coverage_errors:
        warnings.extend([f"Coverage warning: {e}" for e in coverage_errors])
    
    low_support_fields = [
        f.name for f in fields
        if f.inference_confidence < config.boundary_support_min
    ]
    if low_support_fields:
        warnings.append(
            f"Low confidence boundaries for fields: {', '.join(low_support_fields)}. "
            "These field positions may be incorrect."
        )
    
    # Step 10: Check if should reject
    if confidence < config.infer_min:
        return InferenceResult(
            schema=None,
            confidence=confidence,
            errors=[
                f"Inference confidence too low ({confidence:.2f} < {config.infer_min}). "
                f"{config.remediation_explicit_colspec}"
            ],
            warnings=warnings,
            boundary_supports=boundary_supports,
        )
    
    schema.warnings = warnings
    
    return InferenceResult(
        schema=schema,
        confidence=confidence,
        warnings=warnings,
        boundary_supports=boundary_supports,
    )


def _infer_line_length(
    lines: List[str],
    config: FixedWidthConfidenceConfig,
) -> Optional[int]:
    """Infer the consistent line length."""
    if not lines:
        return None
    
    lengths = [len(line) for line in lines]
    length_counts = Counter(lengths)
    
    most_common_length, most_common_count = length_counts.most_common(1)[0]
    consistency = most_common_count / len(lengths)
    
    if consistency < (1.0 - config.line_length_variance_max):
        return None
    
    return most_common_length


def _infer_colspec_with_support(
    lines: List[str],
    line_length: int,
    config: FixedWidthConfidenceConfig,
) -> Tuple[Optional[List[Tuple[int, int]]], Dict[int, float], float]:
    """
    Infer column spec with support scores for each boundary.
    
    Returns:
        (colspec, boundary_supports, overall_confidence)
        
    colspec: List of (start, length) tuples, or None if inference fails
    boundary_supports: Dict of position -> support score
    overall_confidence: Confidence in the boundary detection
    """
    if not lines:
        return None, {}, 0.0
    
    n_lines = len(lines)
    
    # Count boundary positions (transitions between data and whitespace)
    boundary_counts = Counter()
    
    for line in lines:
        in_whitespace = False
        for i in range(line_length):
            if i < len(line):
                is_space = line[i] == ' '
            else:
                is_space = True
            
            if i > 0:
                was_space = line[i - 1] == ' ' if i - 1 < len(line) else True
                # Transition from non-space to space or vice versa
                if is_space != was_space:
                    boundary_counts[i] += 1
    
    if not boundary_counts:
        return None, {}, 0.0
    
    # Calculate support for each position
    boundary_supports = {
        pos: count / n_lines
        for pos, count in boundary_counts.items()
    }
    
    # Keep boundaries with sufficient support
    high_support = [
        pos for pos, support in boundary_supports.items()
        if support >= config.boundary_support_min
    ]
    
    if not high_support:
        # Fall back to lower threshold
        lower_threshold = config.boundary_support_min * 0.7
        high_support = [
            pos for pos, support in boundary_supports.items()
            if support >= lower_threshold
        ]
        if not high_support:
            return None, boundary_supports, 0.0
    
    # Sort boundaries
    high_support = sorted(high_support)
    
    # Build colspec from boundaries
    # Always start at 0
    if high_support[0] != 0:
        high_support = [0] + high_support
    
    # Always end at line_length
    if high_support[-1] != line_length:
        high_support.append(line_length)
    
    # Convert to (start, length) tuples
    colspec = []
    for i in range(len(high_support) - 1):
        start = high_support[i]
        end = high_support[i + 1]
        length = end - start
        if length > 0:
            colspec.append((start, length))
    
    # Calculate overall confidence
    if len(colspec) < 2:
        return None, boundary_supports, 0.0
    
    # Mean support of kept boundaries
    kept_supports = [
        boundary_supports.get(start, 0.8)
        for start, _ in colspec
        if start in boundary_supports
    ]
    if kept_supports:
        overall_confidence = sum(kept_supports) / len(kept_supports)
    else:
        overall_confidence = 0.6
    
    return colspec, boundary_supports, overall_confidence


def _detect_field_types(
    lines: List[str],
    colspec: List[Tuple[int, int]],
    line_length: int,
    max_samples: int = 100,
) -> List[str]:
    """Detect data types for each field."""
    sample_lines = lines[:max_samples]
    types = []
    
    for start, length in colspec:
        values = []
        for line in sample_lines:
            if len(line) >= start + length:
                value = line[start:start + length].strip()
                if value:
                    values.append(value)
        types.append(_infer_type_from_values(values))
    
    return types


def _infer_type_from_values(values: List[str]) -> str:
    """Infer field type from sample values."""
    if not values:
        return "string"
    
    # Try integer (including signed and zero-padded)
    int_count = 0
    for v in values:
        cleaned = v.lstrip('+-0') or '0'
        try:
            int(cleaned)
            int_count += 1
        except ValueError:
            pass
    
    if int_count == len(values):
        return "integer"
    
    # Try decimal
    decimal_count = 0
    for v in values:
        try:
            float(v.replace(',', ''))
            decimal_count += 1
        except ValueError:
            pass
    
    if decimal_count == len(values):
        return "decimal"
    
    # Try date patterns
    date_patterns = [
        r'^\d{4}-\d{2}-\d{2}$',  # 2024-01-15
        r'^\d{2}/\d{2}/\d{4}$',  # 01/15/2024
        r'^\d{8}$',              # 20240115
        r'^\d{2}-\d{2}-\d{4}$',  # 01-15-2024
        r'^\d{4}\d{2}\d{2}$',    # YYYYMMDD
    ]
    
    for pattern in date_patterns:
        if all(re.match(pattern, v) for v in values if v):
            return "date"
    
    # Try boolean
    bool_values = {'true', 'false', 'yes', 'no', 'y', 'n', '0', '1', 't', 'f'}
    if all(v.lower() in bool_values for v in values):
        return "boolean"
    
    return "string"


def _extract_samples(
    lines: List[str],
    start: int,
    length: int,
    max_samples: int = 5,
) -> List[str]:
    """Extract unique sample values for a field."""
    samples = []
    seen = set()
    
    for line in lines:
        if len(line) >= start + length:
            value = line[start:start + length].strip()
            if value and value not in seen:
                seen.add(value)
                samples.append(value)
                if len(samples) >= max_samples:
                    break
    
    return samples


# =============================================================================
# Parsing with Schema
# =============================================================================

def parse_with_schema(
    content: str,
    schema: FixedWidthSchema,
    *,
    on_drift: str = "warn",  # "warn", "skip", "error"
) -> Tuple[List[Dict[str, str]], List[str]]:
    """
    Parse fixed-width content using an explicit schema.
    
    Args:
        content: File content
        schema: FixedWidthSchema to use for parsing
        on_drift: How to handle lines that don't match schema:
            - "warn": Include with warning
            - "skip": Skip the line
            - "error": Return error
            
    Returns:
        (rows, warnings) where rows is list of dicts and warnings is list of strings
    """
    lines = content.split('\n')
    non_empty = [line.rstrip('\r') for line in lines if line.strip()]
    
    rows = []
    warnings = []
    
    for i, line in enumerate(non_empty):
        line_num = i + 1
        
        # Check line length
        if len(line) != schema.line_length:
            msg = f"Line {line_num}: length {len(line)} != expected {schema.line_length}"
            if on_drift == "error":
                warnings.append(f"ERROR: {msg}")
                return rows, warnings
            elif on_drift == "skip":
                warnings.append(f"SKIP: {msg}")
                continue
            else:  # warn
                warnings.append(f"WARN: {msg}")
        
        # Extract fields
        row = {}
        for field in schema.fields:
            if field.end <= len(line):
                value = line[field.start:field.end].strip()
            elif field.start < len(line):
                value = line[field.start:].strip()
                warnings.append(
                    f"Line {line_num}: field '{field.name}' truncated "
                    f"(expected {field.length} chars, got {len(line) - field.start})"
                )
            else:
                value = ""
                warnings.append(
                    f"Line {line_num}: field '{field.name}' missing "
                    f"(starts at {field.start}, line length {len(line)})"
                )
            
            row[field.name] = value
        
        rows.append(row)
    
    return rows, warnings


# =============================================================================
# Legacy Compatibility Functions
# =============================================================================

def infer_line_length(content: str, tolerance: int = 0) -> Optional[int]:
    """
    Legacy: Infer the fixed-width line length from content.
    
    Prefer using infer_schema() which provides confidence scoring.
    """
    config = get_config()
    lines = content.split('\n')
    non_empty = [line.rstrip('\r') for line in lines if line.strip()]
    return _infer_line_length(non_empty, config)


def infer_colspec_from_whitespace(
    content: str,
    line_length: int,
    min_samples: int = 5,
) -> Optional[List[Tuple[int, int]]]:
    """
    Legacy: Infer column boundaries from whitespace patterns.
    
    Prefer using infer_schema() which provides confidence scoring.
    """
    config = get_config()
    config.min_rows = min_samples
    
    lines = content.split('\n')
    target_lines = [
        line.rstrip('\r')
        for line in lines
        if len(line.rstrip('\r')) == line_length
    ]
    
    colspec, _, _ = _infer_colspec_with_support(target_lines, line_length, config)
    return colspec


def detect_field_types(
    content: str,
    colspec: List[Tuple[int, int]],
    line_length: int,
    max_samples: int = 100,
) -> List[str]:
    """
    Legacy: Detect data types for each field.
    
    Prefer using infer_schema() which provides confidence scoring.
    """
    lines = content.split('\n')
    target_lines = [
        line.rstrip('\r')
        for line in lines
        if len(line.rstrip('\r')) == line_length
    ]
    return _detect_field_types(target_lines, colspec, line_length, max_samples)


def parse_with_explicit_colspec(
    content: str,
    colspec: List[Tuple[int, int]],
    field_names: Optional[List[str]] = None,
    skip_header: bool = False,
) -> FixedWidthSchema:
    """
    Legacy: Parse fixed-width content with explicit column specification.
    
    Prefer using infer_schema() + parse_with_schema() for new code.
    """
    line_length = infer_line_length(content)
    
    if line_length is None:
        return FixedWidthSchema(
            line_length=0,
            fields=[],
            errors=["Could not determine consistent line length"],
        )
    
    # Validate colspec
    total_coverage = sum(length for _, length in colspec)
    if total_coverage > line_length:
        return FixedWidthSchema(
            line_length=line_length,
            fields=[],
            errors=[
                f"Colspec total ({total_coverage}) exceeds line length ({line_length})"
            ],
        )
    
    # Detect types
    types = detect_field_types(content, colspec, line_length)
    
    # Build field names
    if field_names is None:
        field_names = [f"field_{i}" for i in range(len(colspec))]
    
    if len(field_names) != len(colspec):
        return FixedWidthSchema(
            line_length=line_length,
            fields=[],
            errors=[
                f"Field names count ({len(field_names)}) "
                f"doesn't match colspec count ({len(colspec)})"
            ],
        )
    
    # Get sample values
    lines = content.split('\n')
    valid_lines = [
        line.rstrip('\r')
        for line in lines
        if len(line.rstrip('\r')) == line_length
    ]
    if skip_header and valid_lines:
        valid_lines = valid_lines[1:]
    
    # Build fields
    fields = []
    for i, ((start, length), name, ftype) in enumerate(zip(colspec, field_names, types)):
        samples = _extract_samples(valid_lines, start, length)
        fields.append(FixedWidthField(
            name=name,
            start=start,
            length=length,
            inferred_type=ftype,
            sample_values=samples,
        ))
    
    schema = FixedWidthSchema(
        line_length=line_length,
        fields=fields,
        has_header=skip_header,
    )
    
    coverage_errors = schema.validate_coverage()
    schema.errors.extend(coverage_errors)
    
    return schema


def infer_fixed_width_schema(
    content: str,
    min_line_samples: int = 5,
    require_full_coverage: bool = True,
) -> FixedWidthSchema:
    """
    Legacy: Attempt to infer a complete fixed-width schema.
    
    Prefer using infer_schema() which provides confidence scoring.
    """
    config = get_config()
    config.min_rows = min_line_samples
    
    result = infer_schema(content, config=config, require_full_coverage=require_full_coverage)
    
    if result.schema is not None:
        return result.schema
    else:
        return FixedWidthSchema(
            line_length=0,
            fields=[],
            errors=result.errors,
        )


def is_likely_fixed_width(
    content: str,
    min_lines: int = 3,
) -> Tuple[bool, float, str]:
    """
    Legacy: Quick check if content is likely fixed-width format.
    
    Prefer using detect_with_confidence() for new code.
    """
    config = get_config()
    config.min_rows = min_lines
    
    result = detect_with_confidence(content, config=config)
    
    reason = "; ".join(result.reasons[:2]) if result.reasons else "unknown"
    return result.matched, result.confidence, reason


__all__ = [
    # Data classes
    "FixedWidthField",
    "FixedWidthSchema",
    
    # New rich APIs
    "detect_with_confidence",
    "infer_schema",
    "parse_with_schema",
    
    # Legacy APIs (for compatibility)
    "infer_line_length",
    "infer_colspec_from_whitespace",
    "detect_field_types",
    "parse_with_explicit_colspec",
    "infer_fixed_width_schema",
    "is_likely_fixed_width",
]
