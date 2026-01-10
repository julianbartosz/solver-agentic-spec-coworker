"""
Human Edit Models (PR #11)

Patch-based human edit capability with strict validation.

This module defines:
- HumanEditPatch: The unified diff patch model
- EditValidationResult: Validation outcome with bounded errors/warnings
- PatchParseError: Exception for parse failures
- EditAuditEntry: Audit record for each applied patch

Per ADR-HITL-ENHANCEMENT-v2 PR #11:
- UI submits unified diff patches, NOT full file blobs
- Patches are validated before application
- All edits are audited
- State keeps only refs + bounded summary

NON-NEGOTIABLE INVARIANTS:
1. Single-file patches only (no multi-file)
2. Hunk context must match or hard fail (no fuzzy matching)
3. Bounded: max hunks, max lines, max bytes
4. Python files: AST parse before accepting
5. Audit artifact required for every edit

CRITICAL: Importing this module must NOT import:
- Streamlit
- LangGraph internals
- Persistence backends
"""

import ast
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple

# Import bounds from single source of truth
from integration_coworker.graph.production_guardrails import (
    MAX_FILE_PATH_LENGTH,
)
from integration_coworker.graph.regeneration_models import (
    validate_rel_path,
    PathValidationError,
)


# =============================================================================
# Decision Schema Version
# =============================================================================

# CRITICAL: Bump this when changing decision schema (add fields, change semantics)
# The graph should validate that incoming decisions match expected version
DECISION_SCHEMA_VERSION = 1

# Valid actions for sandbox review decisions
VALID_SANDBOX_ACTIONS = frozenset({
    "continue",            # Approve and proceed
    "regenerate_targeted", # Request targeted regeneration with feedback
    "apply_human_edits",   # Apply human-authored patches
})


# =============================================================================
# Bounds Constants
# =============================================================================

# Maximum number of hunks in a single patch
MAX_HUNKS_PER_PATCH = 10

# Maximum total lines added/removed
MAX_CHANGED_LINES = 200

# Maximum patch size in bytes
MAX_PATCH_BYTES = 32_768  # 32KB

# Maximum errors/warnings in validation result
MAX_VALIDATION_MESSAGES = 10

# Maximum reason length for audit
MAX_EDIT_REASON_LENGTH = 1000

# Maximum editor identity length
MAX_EDITOR_ID_LENGTH = 100


# =============================================================================
# Exceptions
# =============================================================================

class PatchParseError(ValueError):
    """Raised when a patch cannot be parsed."""
    pass


class PatchApplyError(ValueError):
    """Raised when a patch cannot be applied (context mismatch)."""
    pass


class EditValidationError(ValueError):
    """Raised when edit validation fails."""
    pass


class DecisionSchemaError(ValueError):
    """Raised when a decision has invalid schema or version mismatch."""
    pass


# =============================================================================
# Decision Validation
# =============================================================================

def validate_sandbox_decision(decision: Dict[str, Any]) -> List[str]:
    """
    Validate a sandbox review decision conforms to schema.
    
    Returns list of validation errors (empty if valid).
    
    Schema v1:
        {
            "action": str (required, one of VALID_SANDBOX_ACTIONS),
            "decision_version": int (optional, defaults to 1),
            "decided_at": float (required, timestamp),
            "patches": list[dict] (required if action=apply_human_edits),
            "feedback": str (optional),
            "targets": list[str] (optional, file paths for regeneration),
        }
    """
    errors = []
    
    # Check version compatibility
    version = decision.get("decision_version", 1)
    if version > DECISION_SCHEMA_VERSION:
        errors.append(
            f"Decision schema version {version} > supported {DECISION_SCHEMA_VERSION}"
        )
        return errors  # Can't validate future schema
    
    # Required fields
    action = decision.get("action")
    if not action:
        errors.append("Missing required field: action")
    elif action not in VALID_SANDBOX_ACTIONS:
        errors.append(f"Invalid action: {action}. Valid: {sorted(VALID_SANDBOX_ACTIONS)}")
    
    decided_at = decision.get("decided_at")
    if not decided_at:
        errors.append("Missing required field: decided_at")
    elif not isinstance(decided_at, (int, float)):
        errors.append(f"decided_at must be numeric timestamp, got {type(decided_at).__name__}")
    
    # Action-specific validation
    if action == "apply_human_edits":
        patches = decision.get("patches")
        if not patches:
            errors.append("apply_human_edits action requires non-empty patches list")
        elif not isinstance(patches, list):
            errors.append(f"patches must be a list, got {type(patches).__name__}")
        else:
            for i, p in enumerate(patches):
                if not isinstance(p, dict):
                    errors.append(f"patches[{i}] must be a dict")
                    continue
                if "file_path" not in p:
                    errors.append(f"patches[{i}] missing required field: file_path")
                if "patch_text" not in p:
                    errors.append(f"patches[{i}] missing required field: patch_text")
                if "reason" not in p:
                    errors.append(f"patches[{i}] missing required field: reason")
    
    return errors


def create_sandbox_decision(
    action: str,
    patches: Optional[List[Dict[str, Any]]] = None,
    feedback: Optional[str] = None,
    targets: Optional[List[str]] = None,
    decided_at: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Create a validated sandbox decision with proper schema version.
    
    Args:
        action: One of VALID_SANDBOX_ACTIONS
        patches: List of patch dicts (required for apply_human_edits)
        feedback: Optional human feedback text
        targets: Optional list of file paths for regeneration
        decided_at: Timestamp (defaults to now)
        
    Returns:
        Valid decision dict
        
    Raises:
        DecisionSchemaError: If validation fails
    """
    import time
    
    decision = {
        "action": action,
        "decision_version": DECISION_SCHEMA_VERSION,
        "decided_at": decided_at or time.time(),
    }
    
    if feedback:
        decision["feedback"] = feedback
    if targets:
        decision["targets"] = targets
    if patches:
        decision["patches"] = patches
    
    # Validate before returning
    errors = validate_sandbox_decision(decision)
    if errors:
        raise DecisionSchemaError(f"Invalid decision: {'; '.join(errors)}")
    
    return decision


# =============================================================================
# Validation Result
# =============================================================================

@dataclass
class EditValidationResult:
    """
    Result of validating a human edit patch.
    
    Attributes:
        valid: Whether the patch is valid and can be applied
        errors: List of blocking errors (validation failed)
        warnings: List of non-blocking warnings
    """
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        # Bound the number of messages
        self.errors = self.errors[:MAX_VALIDATION_MESSAGES]
        self.warnings = self.warnings[:MAX_VALIDATION_MESSAGES]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EditValidationResult":
        return cls(
            valid=data["valid"],
            errors=data.get("errors", []),
            warnings=data.get("warnings", []),
        )


# =============================================================================
# Patch Hunk Model
# =============================================================================

@dataclass
class PatchHunk:
    """
    A single hunk in a unified diff patch.
    
    Attributes:
        old_start: Starting line in original file (1-indexed)
        old_count: Number of lines from original
        new_start: Starting line in patched file (1-indexed)
        new_count: Number of lines in patched version
        content: The hunk content (context and changes)
    """
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    content: List[str]  # Lines starting with ' ', '+', or '-'
    
    def count_changes(self) -> Tuple[int, int]:
        """Return (added, removed) line counts."""
        added = sum(1 for line in self.content if line.startswith('+'))
        removed = sum(1 for line in self.content if line.startswith('-'))
        return added, removed


# =============================================================================
# Human Edit Patch Model
# =============================================================================

@dataclass
class HumanEditPatch:
    """
    A unified diff patch for a single file.
    
    Attributes:
        file_path: Relative path to the file (validated)
        patch_text: The raw unified diff text
        reason: Human-provided reason for the edit
        editor_id: Identity of the editor (optional)
        expected_base_sha256: SHA256 of file content UI was viewing (concurrency control)
        timestamp: When the edit was submitted
        hunks: Parsed hunks (populated by parse())
    
    Optimistic Concurrency Control:
        If expected_base_sha256 is provided, validation will fail if the
        current file content has a different hash. This prevents applying
        patches based on stale file views.
    
    Bounds:
        - file_path: MAX_FILE_PATH_LENGTH chars
        - patch_text: MAX_PATCH_BYTES bytes
        - reason: MAX_EDIT_REASON_LENGTH chars
        - hunks: MAX_HUNKS_PER_PATCH
        - total changes: MAX_CHANGED_LINES
    """
    file_path: str
    patch_text: str
    reason: str
    editor_id: Optional[str] = None
    expected_base_sha256: Optional[str] = None  # Optimistic concurrency
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    hunks: List[PatchHunk] = field(default_factory=list)
    
    def __post_init__(self):
        # Validate and normalize file path
        try:
            self.file_path = validate_rel_path(self.file_path, "file_path")
        except PathValidationError as e:
            raise EditValidationError(str(e))
        
        # Bound patch text
        if len(self.patch_text.encode('utf-8')) > MAX_PATCH_BYTES:
            raise EditValidationError(
                f"Patch exceeds {MAX_PATCH_BYTES} bytes"
            )
        
        # Bound reason
        if len(self.reason) > MAX_EDIT_REASON_LENGTH:
            self.reason = self.reason[:MAX_EDIT_REASON_LENGTH]
        
        # Bound editor_id
        if self.editor_id and len(self.editor_id) > MAX_EDITOR_ID_LENGTH:
            self.editor_id = self.editor_id[:MAX_EDITOR_ID_LENGTH]
        
        # Validate expected_base_sha256 format if provided
        if self.expected_base_sha256:
            if len(self.expected_base_sha256) != 64 or not all(
                c in '0123456789abcdef' for c in self.expected_base_sha256.lower()
            ):
                raise EditValidationError(
                    "expected_base_sha256 must be a 64-character hex string"
                )
            self.expected_base_sha256 = self.expected_base_sha256.lower()
    
    def parse(self) -> None:
        """
        Parse the patch_text into hunks.
        
        Raises:
            PatchParseError: If patch cannot be parsed
        """
        self.hunks = _parse_unified_diff(self.patch_text)
        
        # Validate hunk count
        if len(self.hunks) > MAX_HUNKS_PER_PATCH:
            raise PatchParseError(
                f"Patch has {len(self.hunks)} hunks, max is {MAX_HUNKS_PER_PATCH}"
            )
        
        # Validate total changes
        total_added = 0
        total_removed = 0
        for hunk in self.hunks:
            added, removed = hunk.count_changes()
            total_added += added
            total_removed += removed
        
        if total_added + total_removed > MAX_CHANGED_LINES:
            raise PatchParseError(
                f"Patch changes {total_added + total_removed} lines, "
                f"max is {MAX_CHANGED_LINES}"
            )

    
    def fingerprint(self) -> str:
        """
        Compute deterministic fingerprint of the patch.
        
        Used for deduplication and audit.
        """
        canonical = json.dumps({
            "file_path": self.file_path,
            "patch_text": self.patch_text,
        }, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "file_path": self.file_path,
            "patch_text": self.patch_text,
            "reason": self.reason,
            "editor_id": self.editor_id,
            "timestamp": self.timestamp.isoformat(),
            "fingerprint": self.fingerprint(),
        }
        # Include expected_base_sha256 for concurrency control
        if self.expected_base_sha256:
            result["expected_base_sha256"] = self.expected_base_sha256
        return result
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HumanEditPatch":
        timestamp = data.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        elif timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        patch = cls(
            file_path=data["file_path"],
            patch_text=data["patch_text"],
            reason=data.get("reason", ""),
            editor_id=data.get("editor_id"),
            expected_base_sha256=data.get("expected_base_sha256"),
            timestamp=timestamp,
        )
        patch.parse()
        return patch


# =============================================================================
# Audit Entry Model
# =============================================================================

@dataclass
class EditAuditEntry:
    """
    Audit record for a single human edit.
    
    Stored as artifact for compliance and debugging.
    """
    run_id: str
    file_path: str
    patch_fingerprint: str
    reason: str
    editor_id: Optional[str]
    timestamp: datetime
    validation_errors: List[str]
    validation_warnings: List[str]
    applied: bool
    lines_added: int
    lines_removed: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "file_path": self.file_path,
            "patch_fingerprint": self.patch_fingerprint,
            "reason": self.reason,
            "editor_id": self.editor_id,
            "timestamp": self.timestamp.isoformat(),
            "validation_errors": self.validation_errors[:MAX_VALIDATION_MESSAGES],
            "validation_warnings": self.validation_warnings[:MAX_VALIDATION_MESSAGES],
            "applied": self.applied,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
        }
    
    @classmethod
    def from_patch(
        cls,
        run_id: str,
        patch: HumanEditPatch,
        validation: EditValidationResult,
        applied: bool,
    ) -> "EditAuditEntry":
        """Create audit entry from patch and validation result."""
        total_added = 0
        total_removed = 0
        for hunk in patch.hunks:
            added, removed = hunk.count_changes()
            total_added += added
            total_removed += removed
        
        return cls(
            run_id=run_id,
            file_path=patch.file_path,
            patch_fingerprint=patch.fingerprint(),
            reason=patch.reason,
            editor_id=patch.editor_id,
            timestamp=patch.timestamp,
            validation_errors=validation.errors,
            validation_warnings=validation.warnings,
            applied=applied,
            lines_added=total_added,
            lines_removed=total_removed,
        )


# =============================================================================
# Patch Parsing (Unified Diff)
# =============================================================================

# Regex for hunk header: @@ -old_start,old_count +new_start,new_count @@
HUNK_HEADER_RE = re.compile(
    r'^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@'
)


def _parse_unified_diff(patch_text: str) -> List[PatchHunk]:
    """
    Parse a unified diff into hunks.
    
    Strict parsing: no fuzzy matching, context must be exact.
    
    Args:
        patch_text: The unified diff text
        
    Returns:
        List of PatchHunk objects
        
    Raises:
        PatchParseError: If patch cannot be parsed
    """
    lines = patch_text.splitlines()
    hunks = []
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Skip headers (---, +++)
        if line.startswith('---') or line.startswith('+++'):
            i += 1
            continue
        
        # Look for hunk header
        match = HUNK_HEADER_RE.match(line)
        if match:
            old_start = int(match.group(1))
            old_count = int(match.group(2)) if match.group(2) else 1
            new_start = int(match.group(3))
            new_count = int(match.group(4)) if match.group(4) else 1
            
            # Collect hunk content
            i += 1
            content = []
            while i < len(lines):
                content_line = lines[i]
                # Hunk content: context (' '), add ('+'), remove ('-')
                if content_line.startswith(' ') or \
                   content_line.startswith('+') or \
                   content_line.startswith('-'):
                    content.append(content_line)
                    i += 1
                elif content_line.startswith('\\'):
                    # "\ No newline at end of file" - skip
                    i += 1
                else:
                    # End of hunk
                    break
            
            hunks.append(PatchHunk(
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                content=content,
            ))
        else:
            i += 1
    
    if not hunks:
        raise PatchParseError("No hunks found in patch")
    
    return hunks


# =============================================================================
# Patch Validation
# =============================================================================

def validate_patch(
    patch: HumanEditPatch,
    original_content: str,
) -> EditValidationResult:
    """
    Validate that a patch can be applied to the original content.
    
    Checks:
    1. Optimistic concurrency: expected_base_sha256 matches current content
    2. Patch parses correctly
    3. Context lines match original content
    4. Python files: result has valid syntax
    5. Patch is bounded (already enforced in model)
    
    Args:
        patch: The patch to validate
        original_content: The current file content
        
    Returns:
        EditValidationResult with valid=True if patch can be applied
    """
    errors = []
    warnings = []
    
    # STEP 1: Optimistic concurrency check
    if patch.expected_base_sha256:
        current_sha256 = hashlib.sha256(original_content.encode('utf-8')).hexdigest()
        if current_sha256 != patch.expected_base_sha256:
            return EditValidationResult(
                valid=False,
                errors=[
                    f"File content changed since patch was created. "
                    f"Expected: {patch.expected_base_sha256[:12]}..., "
                    f"Current: {current_sha256[:12]}... "
                    f"(optimistic concurrency check failed)"
                ]
            )
    
    # Parse the patch if not already parsed
    if not patch.hunks:
        try:
            patch.parse()
        except PatchParseError as e:
            return EditValidationResult(valid=False, errors=[str(e)])
    
    # Try to apply the patch (dry run)
    try:
        result_content = apply_patch(patch, original_content)
    except PatchApplyError as e:
        return EditValidationResult(valid=False, errors=[str(e)])
    
    # Python syntax validation
    if patch.file_path.endswith('.py'):
        try:
            ast.parse(result_content)
        except SyntaxError as e:
            errors.append(f"Patched code has syntax error: {e.msg} at line {e.lineno}")
            return EditValidationResult(valid=False, errors=errors, warnings=warnings)
    
    # Size check warning
    original_lines = len(original_content.splitlines())
    result_lines = len(result_content.splitlines())
    if result_lines > original_lines * 2:
        warnings.append(
            f"Patch significantly increases file size "
            f"({original_lines} → {result_lines} lines) - consider regeneration"
        )
    
    return EditValidationResult(valid=True, errors=errors, warnings=warnings)


# =============================================================================
# Patch Application
# =============================================================================

def apply_patch(patch: HumanEditPatch, original_content: str) -> str:
    """
    Apply a unified diff patch to content.
    
    STRICT: Context lines must match exactly. No fuzzy matching.
    
    Args:
        patch: The validated patch
        original_content: Original file content
        
    Returns:
        Patched content
        
    Raises:
        PatchApplyError: If context doesn't match
    """
    if not patch.hunks:
        patch.parse()
    
    lines = original_content.splitlines(keepends=True)
    
    # Apply hunks in reverse order to preserve line numbers
    # Sort by old_start descending
    sorted_hunks = sorted(patch.hunks, key=lambda h: h.old_start, reverse=True)
    
    for hunk in sorted_hunks:
        lines = _apply_hunk(lines, hunk)
    
    result = ''.join(lines)
    
    # Ensure consistent trailing newline
    if original_content.endswith('\n') and not result.endswith('\n'):
        result += '\n'
    elif not original_content.endswith('\n') and result.endswith('\n'):
        result = result.rstrip('\n')
    
    return result


def _apply_hunk(lines: List[str], hunk: PatchHunk) -> List[str]:
    """
    Apply a single hunk to the lines.
    
    Strict context matching: context lines must match exactly.
    """
    # Collect expected context and changes
    context_and_removes = []  # Lines that should exist in original
    new_lines = []  # Lines to insert
    
    for line in hunk.content:
        if line.startswith(' '):
            # Context line - should exist and be preserved
            context_and_removes.append(line[1:])  # Strip prefix
            new_lines.append(line[1:])
        elif line.startswith('-'):
            # Remove line - should exist
            context_and_removes.append(line[1:])
        elif line.startswith('+'):
            # Add line
            new_lines.append(line[1:])
    
    # Find the hunk location (0-indexed)
    start_idx = hunk.old_start - 1
    
    # Verify we have enough lines
    if start_idx < 0 or start_idx + len(context_and_removes) > len(lines):
        raise PatchApplyError(
            f"Hunk at line {hunk.old_start} extends beyond file "
            f"({len(lines)} lines)"
        )
    
    # Verify context matches
    for i, expected in enumerate(context_and_removes):
        actual_idx = start_idx + i
        actual = lines[actual_idx].rstrip('\n')
        expected_stripped = expected.rstrip('\n')
        
        if actual != expected_stripped:
            raise PatchApplyError(
                f"Context mismatch at line {actual_idx + 1}: "
                f"expected '{expected_stripped[:50]}...', "
                f"got '{actual[:50]}...'"
            )
    
    # Apply: replace old lines with new lines
    # Preserve line endings
    has_newline = lines[start_idx].endswith('\n') if start_idx < len(lines) else True
    
    new_lines_with_endings = []
    for i, line in enumerate(new_lines):
        if i < len(new_lines) - 1 or has_newline:
            if not line.endswith('\n'):
                line += '\n'
        new_lines_with_endings.append(line)
    
    # Replace
    lines[start_idx:start_idx + len(context_and_removes)] = new_lines_with_endings
    
    return lines


# =============================================================================
# Human Edit State Summary
# =============================================================================

@dataclass
class HumanEditSummary:
    """
    Bounded summary of human edits for state storage.
    
    This is what goes in WorkflowState, NOT the full patches.
    """
    edit_count: int
    files_edited: List[str]  # Truncated to first 5
    total_lines_added: int
    total_lines_removed: int
    needs_static_recheck: bool
    
    # Bounds
    MAX_FILES_IN_SUMMARY = 5
    
    def __post_init__(self):
        self.files_edited = self.files_edited[:self.MAX_FILES_IN_SUMMARY]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "edit_count": self.edit_count,
            "files_edited": self.files_edited,
            "total_lines_added": self.total_lines_added,
            "total_lines_removed": self.total_lines_removed,
            "needs_static_recheck": self.needs_static_recheck,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HumanEditSummary":
        return cls(
            edit_count=data["edit_count"],
            files_edited=data.get("files_edited", []),
            total_lines_added=data.get("total_lines_added", 0),
            total_lines_removed=data.get("total_lines_removed", 0),
            needs_static_recheck=data.get("needs_static_recheck", True),
        )
    
    @classmethod
    def from_patches(
        cls,
        patches: List[HumanEditPatch],
        needs_static_recheck: bool = True,
    ) -> "HumanEditSummary":
        """Build summary from list of patches."""
        files = []
        total_added = 0
        total_removed = 0
        
        for patch in patches:
            if patch.file_path not in files:
                files.append(patch.file_path)
            
            for hunk in patch.hunks:
                added, removed = hunk.count_changes()
                total_added += added
                total_removed += removed
        
        return cls(
            edit_count=len(patches),
            files_edited=files,
            total_lines_added=total_added,
            total_lines_removed=total_removed,
            needs_static_recheck=needs_static_recheck,
        )
