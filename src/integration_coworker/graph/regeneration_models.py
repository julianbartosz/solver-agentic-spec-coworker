"""
Targeted Regeneration Models (PR #10)

Decision schemas and validation for targeted code regeneration.

This module defines the typed decision object for `regenerate_targeted` action
with strict validation to prevent:
- State bloat (bounded targets, feedback)
- Security issues (path traversal prevention)
- Non-determinism (sorted ordering at parse time)

Per ADR-HITL-ENHANCEMENT-v2 PR #10:
- All fields must be JSON-serializable
- Paths must be relative (no absolute, no ..)
- Deterministic ordering: targets and dict keys sorted at parse time

CRITICAL: Importing this module must NOT import:
- Streamlit
- LangGraph internals
- Persistence backends
- subprocess or any tool invocation
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional
import hashlib
import json
import re

# Import bounded constants from single source of truth
from integration_coworker.graph.production_guardrails import (
    MAX_TARGETS,
    MAX_FEEDBACK_LENGTH,
    MAX_TARGET_FEEDBACK_LENGTH,
    MAX_FILE_PATH_LENGTH,
)


# =============================================================================
# Constants
# =============================================================================

# Review action types
ReviewAction = Literal["continue", "regenerate_targeted", "reject", "escalate"]

# Maximum iterations for regeneration loop (hard limit)
MAX_REGENERATION_ITERATIONS = 3

# Iteration state summary bounds
MAX_ITERATION_HISTORY_ENTRIES = 5


# =============================================================================
# Path Validation
# =============================================================================

class PathValidationError(ValueError):
    """Raised when a path fails validation."""
    pass


def validate_rel_path(path: str, field_name: str = "path") -> str:
    """
    Validate and normalize a relative path.
    
    Rejects:
    - Absolute paths (starting with / or C:\\ etc)
    - Parent traversal (..)
    - Empty paths
    - Paths exceeding MAX_FILE_PATH_LENGTH
    
    Normalizes:
    - Backslashes to forward slashes
    - Multiple consecutive slashes
    - Leading ./
    
    Args:
        path: Path to validate
        field_name: Field name for error messages
        
    Returns:
        Normalized relative path
        
    Raises:
        PathValidationError: If path is invalid
    """
    if not path or not path.strip():
        raise PathValidationError(f"{field_name}: path cannot be empty")
    
    path = path.strip()
    
    # Normalize separators
    path = path.replace("\\", "/")
    
    # Reject absolute paths
    if path.startswith("/"):
        raise PathValidationError(
            f"{field_name}: absolute paths not allowed (got '{path}')"
        )
    
    # Reject Windows absolute paths (C:, D:, etc.)
    if len(path) >= 2 and path[1] == ":" and path[0].isalpha():
        raise PathValidationError(
            f"{field_name}: absolute paths not allowed (got '{path}')"
        )
    
    # Reject parent traversal
    # Check for .. at start, end, or surrounded by slashes
    if ".." in path.split("/"):
        raise PathValidationError(
            f"{field_name}: parent traversal (..) not allowed (got '{path}')"
        )
    
    # Normalize multiple slashes
    path = re.sub(r"/+", "/", path)
    
    # Remove leading ./
    while path.startswith("./"):
        path = path[2:]
    
    # Remove trailing slash
    path = path.rstrip("/")
    
    # Check length
    if len(path) > MAX_FILE_PATH_LENGTH:
        raise PathValidationError(
            f"{field_name}: path too long ({len(path)} > {MAX_FILE_PATH_LENGTH})"
        )
    
    if not path:
        raise PathValidationError(f"{field_name}: path cannot be empty after normalization")
    
    return path


def validate_paths(paths: List[str], field_name: str = "paths") -> List[str]:
    """
    Validate and normalize a list of paths.
    
    Also:
    - Removes duplicates
    - Sorts for determinism
    - Enforces MAX_TARGETS limit
    
    Returns:
        Sorted list of unique normalized paths
    """
    if not paths:
        return []
    
    if len(paths) > MAX_TARGETS:
        raise PathValidationError(
            f"{field_name}: too many targets ({len(paths)} > {MAX_TARGETS})"
        )
    
    normalized = set()
    for i, path in enumerate(paths):
        try:
            normalized.add(validate_rel_path(path, f"{field_name}[{i}]"))
        except PathValidationError:
            raise
    
    # Sort for determinism
    return sorted(normalized)


# =============================================================================
# Regeneration Decision Schema
# =============================================================================

@dataclass
class RegenerateTargetedDecision:
    """
    Decision schema for targeted regeneration.
    
    This is the validated, normalized form of user input for targeted regeneration.
    All fields are bounded and validated at parse time.
    
    Invariants:
    - targets is sorted and deduplicated
    - target_feedback keys are a subset of targets
    - All paths are relative and normalized
    - All text fields are bounded
    """
    action: Literal["regenerate_targeted"] = "regenerate_targeted"
    
    # Target files for regeneration (sorted, deduplicated)
    targets: List[str] = field(default_factory=list)
    
    # Per-target feedback (keys must be in targets)
    target_feedback: Dict[str, str] = field(default_factory=dict)
    
    # Global feedback for all targets
    global_feedback: Optional[str] = None
    
    # Timestamp for audit trail
    decided_at: Optional[float] = None
    
    def __post_init__(self):
        """Validate and normalize after init."""
        # Targets must be list
        if not isinstance(self.targets, list):
            raise PathValidationError("targets must be a list")
        
        # Validate and normalize targets
        self.targets = validate_paths(self.targets, "targets")
        
        # Validate target_feedback keys are subset of targets
        target_set = set(self.targets)
        for key in self.target_feedback:
            normalized_key = validate_rel_path(key, "target_feedback key")
            if normalized_key not in target_set:
                raise PathValidationError(
                    f"target_feedback key '{key}' not in targets"
                )
        
        # Normalize target_feedback keys and bound values
        normalized_feedback = {}
        for key, value in self.target_feedback.items():
            normalized_key = validate_rel_path(key, "target_feedback key")
            bounded_value = (value or "")[:MAX_TARGET_FEEDBACK_LENGTH]
            if bounded_value:
                normalized_feedback[normalized_key] = bounded_value
        
        # Sort keys for determinism
        self.target_feedback = dict(sorted(normalized_feedback.items()))
        
        # Bound global feedback
        if self.global_feedback:
            self.global_feedback = self.global_feedback[:MAX_FEEDBACK_LENGTH]
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to JSON-serializable dict with deterministic ordering.
        
        Keys are sorted for stable fingerprinting.
        """
        return dict(sorted({
            "action": self.action,
            "decided_at": self.decided_at,
            "global_feedback": self.global_feedback,
            "target_feedback": self.target_feedback,  # Already sorted
            "targets": self.targets,  # Already sorted
        }.items()))
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RegenerateTargetedDecision":
        """
        Create from dict with validation.
        
        Raises:
            PathValidationError: If paths are invalid
            ValueError: If action is wrong
        """
        action = data.get("action")
        if action != "regenerate_targeted":
            raise ValueError(
                f"Invalid action for RegenerateTargetedDecision: {action}"
            )
        
        return cls(
            action="regenerate_targeted",
            targets=data.get("targets", []),
            target_feedback=data.get("target_feedback", {}),
            global_feedback=data.get("global_feedback"),
            decided_at=data.get("decided_at"),
        )
    
    def fingerprint(self) -> str:
        """
        Compute stable fingerprint for idempotency checks.
        
        Same decision should produce same fingerprint across runs.
        """
        # Use only the deterministic parts (not decided_at)
        fingerprint_data = {
            "action": self.action,
            "global_feedback": self.global_feedback,
            "target_feedback": self.target_feedback,
            "targets": self.targets,
        }
        serialized = json.dumps(fingerprint_data, sort_keys=True)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]


# =============================================================================
# Iteration State
# =============================================================================

@dataclass
class IterationHistoryEntry:
    """
    Summary of a single regeneration iteration.
    
    Stored in iteration_state.history for debugging and escalation decisions.
    """
    iteration: int
    targets: List[str]
    outcome: Literal["improved", "no_change", "regressed", "error"]
    blocking_before: int  # Blocking issues before this iteration
    blocking_after: int   # Blocking issues after this iteration
    fingerprint: str      # Decision fingerprint for dedup
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return dict(sorted({
            "blocking_after": self.blocking_after,
            "blocking_before": self.blocking_before,
            "fingerprint": self.fingerprint,
            "iteration": self.iteration,
            "outcome": self.outcome,
            "targets": self.targets,
        }.items()))
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IterationHistoryEntry":
        """Create from dict."""
        return cls(
            iteration=data["iteration"],
            targets=data["targets"],
            outcome=data["outcome"],
            blocking_before=data["blocking_before"],
            blocking_after=data["blocking_after"],
            fingerprint=data["fingerprint"],
        )


@dataclass
class IterationState:
    """
    Tracks regeneration iterations for budget enforcement.
    
    Stored in workflow state (bounded summary only).
    """
    current_iteration: int = 0
    max_iterations: int = MAX_REGENERATION_ITERATIONS
    history: List[IterationHistoryEntry] = field(default_factory=list)
    
    def can_iterate(self) -> bool:
        """Check if another iteration is allowed."""
        return self.current_iteration < self.max_iterations
    
    def should_escalate(self) -> bool:
        """
        Check if we should escalate instead of iterating.
        
        Escalate if:
        - Max iterations reached
        - Last N iterations showed no improvement
        - Same fingerprint repeated (stuck in loop)
        """
        if not self.can_iterate():
            return True
        
        # Check for stuck loop (same fingerprint repeated)
        if len(self.history) >= 2:
            recent_fps = [h.fingerprint for h in self.history[-2:]]
            if len(set(recent_fps)) == 1:
                return True
        
        # Check for no improvement in last 2 iterations
        if len(self.history) >= 2:
            recent = self.history[-2:]
            if all(h.outcome in ("no_change", "regressed") for h in recent):
                return True
        
        return False
    
    def record_iteration(
        self,
        targets: List[str],
        outcome: Literal["improved", "no_change", "regressed", "error"],
        blocking_before: int,
        blocking_after: int,
        fingerprint: str,
    ) -> None:
        """Record an iteration outcome."""
        self.current_iteration += 1
        
        entry = IterationHistoryEntry(
            iteration=self.current_iteration,
            targets=sorted(targets),
            outcome=outcome,
            blocking_before=blocking_before,
            blocking_after=blocking_after,
            fingerprint=fingerprint,
        )
        
        self.history.append(entry)
        
        # Keep bounded history
        if len(self.history) > MAX_ITERATION_HISTORY_ENTRIES:
            self.history = self.history[-MAX_ITERATION_HISTORY_ENTRIES:]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return dict(sorted({
            "current_iteration": self.current_iteration,
            "history": [h.to_dict() for h in self.history],
            "max_iterations": self.max_iterations,
        }.items()))
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IterationState":
        """Create from dict."""
        return cls(
            current_iteration=data.get("current_iteration", 0),
            max_iterations=data.get("max_iterations", MAX_REGENERATION_ITERATIONS),
            history=[
                IterationHistoryEntry.from_dict(h) 
                for h in data.get("history", [])
            ],
        )


# =============================================================================
# Regeneration Constraints (Artifact Schema)
# =============================================================================

@dataclass
class RegenerationConstraints:
    """
    Constraints for targeted regeneration.
    
    This is stored as an artifact (not inline in state).
    State holds only a ref to this artifact.
    """
    # Files to regenerate (sorted)
    target_paths: List[str] = field(default_factory=list)
    
    # Reasons for regeneration per target (from attribution + feedback)
    target_reasons: Dict[str, List[str]] = field(default_factory=dict)
    
    # Whether to preserve non-target files unchanged
    preserve_non_targets: bool = True
    
    # Global regeneration guidance
    global_guidance: Optional[str] = None
    
    # Source fingerprints for deduplication
    decision_fingerprint: str = ""  # From decision
    attribution_fingerprint: str = ""  # From sandbox_attribution
    
    def __post_init__(self):
        """Normalize and sort for determinism."""
        self.target_paths = sorted(set(self.target_paths))
        
        # Sort reasons dict keys and limit reason count
        normalized_reasons = {}
        for path in self.target_paths:
            if path in self.target_reasons:
                reasons = self.target_reasons[path][:5]  # Max 5 reasons per target
                normalized_reasons[path] = reasons
        self.target_reasons = dict(sorted(normalized_reasons.items()))
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return dict(sorted({
            "attribution_fingerprint": self.attribution_fingerprint,
            "decision_fingerprint": self.decision_fingerprint,
            "global_guidance": self.global_guidance,
            "preserve_non_targets": self.preserve_non_targets,
            "target_paths": self.target_paths,
            "target_reasons": self.target_reasons,
        }.items()))
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RegenerationConstraints":
        """Create from dict."""
        return cls(
            target_paths=data.get("target_paths", []),
            target_reasons=data.get("target_reasons", {}),
            preserve_non_targets=data.get("preserve_non_targets", True),
            global_guidance=data.get("global_guidance"),
            decision_fingerprint=data.get("decision_fingerprint", ""),
            attribution_fingerprint=data.get("attribution_fingerprint", ""),
        )
    
    def fingerprint(self) -> str:
        """Compute stable fingerprint for idempotency."""
        data = self.to_dict()
        serialized = json.dumps(data, sort_keys=True)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]


# =============================================================================
# Decision Parsing Utilities
# =============================================================================

def parse_review_decision(raw_decision: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse and validate a review decision from UI or API input.
    
    Handles:
    - continue: pass-through
    - regenerate_targeted: validates via RegenerateTargetedDecision
    - reject: pass-through
    - escalate: pass-through
    
    Returns:
        Validated decision dict
        
    Raises:
        ValueError: If decision is invalid
        PathValidationError: If paths are invalid
    """
    action = raw_decision.get("action")
    
    if action == "regenerate_targeted":
        # Parse and validate via dataclass
        validated = RegenerateTargetedDecision.from_dict(raw_decision)
        return validated.to_dict()
    
    if action in ("continue", "reject", "escalate"):
        # Basic validation only
        return dict(sorted({
            "action": action,
            "decided_at": raw_decision.get("decided_at"),
            "feedback": (raw_decision.get("feedback") or "")[:MAX_FEEDBACK_LENGTH],
            "reason": raw_decision.get("reason"),
        }.items()))
    
    raise ValueError(f"Unknown review action: {action}")
