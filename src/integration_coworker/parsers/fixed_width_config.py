"""
Fixed-width file detection and inference configuration.

Centralized thresholds and knobs for fixed-width handling.
All heuristics reference this config - no magic numbers elsewhere.

These values are tuned to:
- Minimize false positives (CSV/TSV/log misrouting)
- Provide actionable confidence scores and warnings
- Enable explicit override via environment or programmatic config
"""

from dataclasses import dataclass, field
from typing import List, Optional
import os


@dataclass
class FixedWidthConfidenceConfig:
    """
    Configuration for fixed-width detection and inference confidence thresholds.
    
    All thresholds are floats in [0.0, 1.0] range unless otherwise noted.
    """
    
    # === Detection Thresholds ===
    # DETECT_MIN: Minimum confidence to accept as fixed-width
    # If confidence < DETECT_MIN, detection returns False (do not match)
    detect_min: float = 0.90
    
    # === Inference Thresholds ===
    # INFER_MIN: Minimum confidence to produce valid ParsedSpec
    # If confidence < INFER_MIN, return invalid ParsedSpec with errors
    infer_min: float = 0.60
    
    # INFER_WARN: Threshold below which warnings are emitted
    # If INFER_MIN <= confidence < INFER_WARN, proceed but emit warnings
    infer_warn: float = 0.85
    
    # === Boundary Detection ===
    # BOUNDARY_SUPPORT_MIN: Minimum support for a boundary position
    # support(pos) = (lines where pos is boundary) / total_lines
    # Boundaries with support < this are discarded
    boundary_support_min: float = 0.80
    
    # === Sampling Parameters ===
    # Number of lines to sample for inference (max)
    infer_nrows: int = 100
    
    # Minimum lines required for confident inference
    min_rows: int = 5
    
    # Maximum columns to infer (prevents runaway on noisy data)
    max_columns: int = 100
    
    # === Signal Weights for Detection ===
    # These weights are used to compute overall detection confidence
    # from individual signals. Must sum to ~1.0 for interpretability.
    weight_line_consistency: float = 0.35
    weight_delimiter_absence: float = 0.25
    weight_boundary_stability: float = 0.25
    weight_extension_hint: float = 0.15
    
    # === Hard Reject Thresholds ===
    # If delimiter rate exceeds this, reject as delimited (not fixed-width)
    delimiter_rate_max: float = 0.05  # 5% of positions have delimiters
    
    # If line length variance exceeds this ratio, reject
    line_length_variance_max: float = 0.10  # 10% variance
    
    # Minimum consecutive spaces to count as a "whitespace run"
    min_whitespace_run: int = 2
    
    # === Remediation Messages ===
    remediation_explicit_colspec: str = (
        "Provide an explicit colspec via sidecar YAML or configuration. "
        "Format: colspec: [[start, length], [start, length], ...]"
    )
    remediation_increase_samples: str = (
        "Increase infer_nrows to gather more samples for boundary detection."
    )
    remediation_force_fixed_width: str = (
        "If you know this is fixed-width, set force_fixed_width=True in config."
    )
    
    @classmethod
    def from_env(cls) -> "FixedWidthConfidenceConfig":
        """
        Load config from environment variables.
        
        Environment variables (all optional):
        - FIXED_WIDTH_DETECT_MIN
        - FIXED_WIDTH_INFER_MIN
        - FIXED_WIDTH_INFER_WARN
        - FIXED_WIDTH_BOUNDARY_SUPPORT_MIN
        - FIXED_WIDTH_INFER_NROWS
        - FIXED_WIDTH_MIN_ROWS
        - FIXED_WIDTH_MAX_COLUMNS
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
            detect_min=_get_float("FIXED_WIDTH_DETECT_MIN", cls.detect_min),
            infer_min=_get_float("FIXED_WIDTH_INFER_MIN", cls.infer_min),
            infer_warn=_get_float("FIXED_WIDTH_INFER_WARN", cls.infer_warn),
            boundary_support_min=_get_float("FIXED_WIDTH_BOUNDARY_SUPPORT_MIN", cls.boundary_support_min),
            infer_nrows=_get_int("FIXED_WIDTH_INFER_NROWS", cls.infer_nrows),
            min_rows=_get_int("FIXED_WIDTH_MIN_ROWS", cls.min_rows),
            max_columns=_get_int("FIXED_WIDTH_MAX_COLUMNS", cls.max_columns),
        )


@dataclass
class DetectionResult:
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
class InferenceResult:
    """
    Rich inference result with confidence and warnings.
    
    Returned by infer_schema() to separate schema from confidence/warnings.
    """
    schema: Optional["FixedWidthSchema"] = None  # Forward ref, set at runtime
    confidence: float = 0.0
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    boundary_supports: dict = field(default_factory=dict)  # pos -> support score
    
    def is_valid(self) -> bool:
        """Check if inference succeeded (has schema and no errors)."""
        return self.schema is not None and not self.errors
    
    def should_warn(self, config: FixedWidthConfidenceConfig) -> bool:
        """Check if warnings should be emitted based on config."""
        return self.confidence < config.infer_warn
    
    def should_reject(self, config: FixedWidthConfidenceConfig) -> bool:
        """Check if result should be rejected based on config."""
        return self.confidence < config.infer_min


# Default global config (can be overridden)
DEFAULT_CONFIG = FixedWidthConfidenceConfig()


def get_config() -> FixedWidthConfidenceConfig:
    """Get the current configuration (from env or defaults)."""
    return FixedWidthConfidenceConfig.from_env()


__all__ = [
    "FixedWidthConfidenceConfig",
    "DetectionResult",
    "InferenceResult",
    "DEFAULT_CONFIG",
    "get_config",
]
