"""
Quality pipeline domain models.

Pure dataclasses only - no I/O, no subprocess, no tool selection.
These define the contract for quality signals passed between graph nodes
and stored in artifacts.

Per ADR-HITL-ENHANCEMENT-v2 PR #7:
- All fields must be JSON-serializable
- Heavy data (full issue lists) goes to artifacts, not state
- State holds refs + bounded summaries only

CRITICAL: Importing this module must NOT import:
- Streamlit
- LangGraph internals
- Persistence backends
- subprocess or any tool invocation
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

# Import bounded constants from single source of truth
from integration_coworker.graph.production_guardrails import (
    MAX_HINT_LENGTH,
    MAX_FILE_PATH_LENGTH,
    MAX_ERROR_MESSAGE_LENGTH,
    MAX_FIX_HINTS,
    MAX_TARGETS,
    MAX_FEEDBACK_LENGTH,
    MAX_TARGET_FEEDBACK_LENGTH,
    MAX_FILES_IN_SUMMARY as MAX_ISSUES_IN_SUMMARY,  # Renamed for clarity
)


# =============================================================================
# Static Analysis Models
# =============================================================================

@dataclass
class StaticIssue:
    """
    A single static analysis finding.
    
    Represents one issue from syntax checks, linters, or type checkers.
    Must be JSON-serializable - no complex objects.
    """
    severity: Literal["error", "warning", "info"]
    category: Literal["syntax", "import", "type", "lint", "security", "other"]
    file_path: str  # rel_path from code artifacts, max MAX_FILE_PATH_LENGTH
    line_number: int
    column: int
    message: str
    rule_id: Optional[str] = None  # e.g., "E501", "F401", "mypy-error"
    suggested_fix: Optional[str] = None
    auto_fixable: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "severity": self.severity,
            "category": self.category,
            "file_path": self.file_path[:MAX_FILE_PATH_LENGTH],
            "line_number": self.line_number,
            "column": self.column,
            "message": self.message[:MAX_ERROR_MESSAGE_LENGTH],
            "rule_id": self.rule_id,
            "suggested_fix": self.suggested_fix[:MAX_HINT_LENGTH] if self.suggested_fix else None,
            "auto_fixable": self.auto_fixable,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StaticIssue":
        """Create from dict."""
        return cls(
            severity=data["severity"],
            category=data["category"],
            file_path=data["file_path"],
            line_number=data["line_number"],
            column=data["column"],
            message=data["message"],
            rule_id=data.get("rule_id"),
            suggested_fix=data.get("suggested_fix"),
            auto_fixable=data.get("auto_fixable", False),
        )


@dataclass
class StaticAnalysisResult:
    """
    Result of static analysis stage.
    
    Full issue list goes to artifact storage. State holds only summary.
    """
    passed: bool
    issues: List[StaticIssue] = field(default_factory=list)
    blocking_count: int = 0  # Errors that block proceeding
    warning_count: int = 0
    tool_versions: Dict[str, str] = field(default_factory=dict)  # tool -> version
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict (includes all issues)."""
        return {
            "passed": self.passed,
            "issues": [i.to_dict() for i in self.issues],
            "blocking_count": self.blocking_count,
            "warning_count": self.warning_count,
            "tool_versions": self.tool_versions,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StaticAnalysisResult":
        """Create from dict."""
        return cls(
            passed=data["passed"],
            issues=[StaticIssue.from_dict(i) for i in data.get("issues", [])],
            blocking_count=data.get("blocking_count", 0),
            warning_count=data.get("warning_count", 0),
            tool_versions=data.get("tool_versions", {}),
        )


# =============================================================================
# Error Attribution Models
# =============================================================================

@dataclass
class FailureAttribution:
    """
    Links a test failure to specific generated code.
    
    Attribution helps target regeneration to the most likely cause.
    """
    test_name: str
    test_file: str
    error_type: str
    error_message: str  # Bounded to MAX_ERROR_MESSAGE_LENGTH in payload
    
    # Attribution (may be None if attribution failed)
    likely_cause_file: Optional[str] = None  # rel_path
    likely_cause_lines: Optional[tuple] = None  # (start, end) - stored as list in JSON
    confidence: float = 0.0  # 0.0-1.0
    
    # Context for regeneration (bounded)
    fix_hints: List[str] = field(default_factory=list)  # max MAX_FIX_HINTS
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "test_name": self.test_name,
            "test_file": self.test_file[:MAX_FILE_PATH_LENGTH],
            "error_type": self.error_type,
            "error_message": self.error_message[:MAX_ERROR_MESSAGE_LENGTH],
            "likely_cause_file": self.likely_cause_file[:MAX_FILE_PATH_LENGTH] if self.likely_cause_file else None,
            "likely_cause_lines": list(self.likely_cause_lines) if self.likely_cause_lines else None,
            "confidence": self.confidence,
            "fix_hints": [h[:MAX_HINT_LENGTH] for h in self.fix_hints[:MAX_FIX_HINTS]],
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FailureAttribution":
        """Create from dict."""
        lines = data.get("likely_cause_lines")
        return cls(
            test_name=data["test_name"],
            test_file=data["test_file"],
            error_type=data["error_type"],
            error_message=data["error_message"],
            likely_cause_file=data.get("likely_cause_file"),
            likely_cause_lines=tuple(lines) if lines else None,
            confidence=data.get("confidence", 0.0),
            fix_hints=data.get("fix_hints", []),
        )


# =============================================================================
# Quality Score Models
# =============================================================================

@dataclass
class QualityScoreBreakdown:
    """
    Composite quality score with breakdown by dimension.
    
    All scores are 0-100, None means not measured.
    """
    overall: float  # 0-100
    static_analysis: float = 0.0  # 0-100
    test_coverage: Optional[float] = None  # 0-100, None if not measured
    complexity: Optional[float] = None  # 0-100, None if not measured
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "overall": self.overall,
            "static_analysis": self.static_analysis,
            "test_coverage": self.test_coverage,
            "complexity": self.complexity,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QualityScoreBreakdown":
        """Create from dict."""
        return cls(
            overall=data["overall"],
            static_analysis=data.get("static_analysis", 0.0),
            test_coverage=data.get("test_coverage"),
            complexity=data.get("complexity"),
        )


# =============================================================================
# Regeneration Models
# =============================================================================

@dataclass
class RegenerationTarget:
    """
    A file targeted for regeneration.
    
    Used when user selects specific files to regenerate instead of all.
    """
    file_path: str  # rel_path
    reason: str  # Why this file needs regeneration
    failures_linked: List[str] = field(default_factory=list)  # Test names
    priority: int = 0  # Higher = regenerate first
    constraints: List[str] = field(default_factory=list)  # Specific constraints
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "file_path": self.file_path[:MAX_FILE_PATH_LENGTH],
            "reason": self.reason[:MAX_TARGET_FEEDBACK_LENGTH],
            "failures_linked": self.failures_linked,
            "priority": self.priority,
            "constraints": self.constraints,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RegenerationTarget":
        """Create from dict."""
        return cls(
            file_path=data["file_path"],
            reason=data["reason"],
            failures_linked=data.get("failures_linked", []),
            priority=data.get("priority", 0),
            constraints=data.get("constraints", []),
        )


# NOTE: HumanEditPatch moved to human_edit_models.py as single source of truth.
# Import from there: from integration_coworker.graph.human_edit_models import HumanEditPatch

# Deprecated re-export for backward compatibility (will be removed in v2.0)
def __getattr__(name: str):
    """Lazy import with deprecation warning for moved classes."""
    if name == "HumanEditPatch":
        import warnings
        warnings.warn(
            "HumanEditPatch has moved to integration_coworker.graph.human_edit_models. "
            "Update your import: from integration_coworker.graph.human_edit_models import HumanEditPatch. "
            "This re-export will be removed in v2.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        from integration_coworker.graph.human_edit_models import HumanEditPatch
        return HumanEditPatch
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# =============================================================================
# Iteration State Model
# =============================================================================

@dataclass
class IterationState:
    """
    Tracks regeneration iteration budget.
    
    Prevents infinite regeneration loops by enforcing max iterations
    and detecting when quality is not improving.
    """
    iteration_count: int = 0
    max_iterations: int = 3
    quality_history: List[float] = field(default_factory=list)  # Score per iteration
    escalation_reason: Optional[str] = None  # Why we stopped iterating
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "iteration_count": self.iteration_count,
            "max_iterations": self.max_iterations,
            "quality_history": self.quality_history,
            "escalation_reason": self.escalation_reason,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IterationState":
        """Create from dict."""
        return cls(
            iteration_count=data.get("iteration_count", 0),
            max_iterations=data.get("max_iterations", 3),
            quality_history=data.get("quality_history", []),
            escalation_reason=data.get("escalation_reason"),
        )
    
    def should_continue(self) -> tuple:
        """
        Check if regeneration should continue.
        
        Returns (should_continue: bool, reason: str).
        """
        # Hard limit
        if self.iteration_count >= self.max_iterations:
            return False, "max_iterations_reached"
        
        # Quality not improving (need at least 2 data points)
        if len(self.quality_history) >= 2:
            recent = self.quality_history[-2:]
            if recent[-1] <= recent[-2]:
                return False, "quality_not_improving"
        
        return True, "continue"
    
    def record_iteration(self, quality_score: float) -> "IterationState":
        """Record an iteration and return updated state."""
        return IterationState(
            iteration_count=self.iteration_count + 1,
            max_iterations=self.max_iterations,
            quality_history=self.quality_history + [quality_score],
            escalation_reason=self.escalation_reason,
        )
