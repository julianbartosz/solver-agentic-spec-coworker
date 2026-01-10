"""
Quality artifact storage utilities.

Stores quality analysis results externally and returns ArtifactRefs.
Enforces refs-not-blobs discipline: large payloads go to artifact store,
state holds only refs + bounded summaries.

Per ADR-HITL-ENHANCEMENT-v2 PR #7:
- JSON codec only (no pickle for security/portability)
- All refs must roundtrip: from_dict(ref.to_dict()) == ref
- Summaries are bounded to keep checkpoint payloads < 2KB
- Full data (200+ issues, etc.) lives in artifact store

CRITICAL INVARIANTS:
1. QUALITY_ARTIFACT_CODEC = JSON only (no pickle)
2. Summary builders enforce MAX_* constants
3. ArtifactRef.to_dict() roundtrips through JSON
"""

import logging
from typing import Any, Dict, List, Optional

from integration_coworker.persistence.artifacts.fs import get_artifact_store
from integration_coworker.persistence.artifacts.base import ArtifactRef, ArtifactCodec

from integration_coworker.graph.quality_models import (
    StaticAnalysisResult,
    StaticIssue,
    FailureAttribution,
    QualityScoreBreakdown,
    RegenerationTarget,
    IterationState,
    # Bounded constants
    MAX_ISSUES_IN_SUMMARY,
    MAX_FIX_HINTS,
    MAX_HINT_LENGTH,
    MAX_ERROR_MESSAGE_LENGTH,
    MAX_FILE_PATH_LENGTH,
    MAX_TARGETS,
)
# Single source of truth for HumanEditPatch (PR #11)
from integration_coworker.graph.human_edit_models import HumanEditPatch

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

# INVARIANT: Quality artifacts are JSON-only. No pickle for safety/portability.
QUALITY_ARTIFACT_CODEC = ArtifactCodec.JSON

# Schema version for forward compatibility
QUALITY_SCHEMA_VERSION = "1.0"


# =============================================================================
# Storage Functions
# =============================================================================

def store_static_analysis_artifact(
    run_id: str,
    result: StaticAnalysisResult,
) -> Dict[str, Any]:
    """
    Store static analysis result and return refs + bounded summary.
    
    The full issue list goes to artifact storage. Returns a dict suitable
    for storing in state.quality_refs["static"].
    
    Returns:
        {
            "result": ArtifactRef.to_dict(),  # Full result
            "summary": {...},                  # Bounded summary (< 1KB)
            "schema_version": "1.0"
        }
    """
    store = get_artifact_store()
    
    # Store full result as artifact
    result_ref = store.put(
        run_id,
        "static_analysis_result",
        result.to_dict(),
        codec=QUALITY_ARTIFACT_CODEC,
    )
    
    logger.debug(
        f"Stored static analysis artifact: {len(result.issues)} issues, "
        f"ref={result_ref.sha256[:8]}..."
    )
    
    return {
        "result": result_ref.to_dict(),
        "summary": build_static_analysis_summary(result),
        "schema_version": QUALITY_SCHEMA_VERSION,
    }


def store_failure_attributions_artifact(
    run_id: str,
    attributions: List[FailureAttribution],
) -> Dict[str, Any]:
    """
    Store failure attributions and return refs + bounded summary.
    
    Returns:
        {
            "failures": ArtifactRef.to_dict(),  # Full attribution list
            "summary": {...},                    # Bounded summary
            "schema_version": "1.0"
        }
    """
    store = get_artifact_store()
    
    # Store full attributions as artifact
    failures_ref = store.put(
        run_id,
        "sandbox_attributions",
        [a.to_dict() for a in attributions],
        codec=QUALITY_ARTIFACT_CODEC,
    )
    
    logger.debug(
        f"Stored {len(attributions)} failure attributions, "
        f"ref={failures_ref.sha256[:8]}..."
    )
    
    return {
        "failures": failures_ref.to_dict(),
        "summary": build_attribution_summary(attributions),
        "schema_version": QUALITY_SCHEMA_VERSION,
    }


def store_human_edits_artifact(
    run_id: str,
    audit_entries: List[Dict[str, Any]],
    patches: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Store human edit audit entries and patches for audit trail.
    
    PR #11: Updated to accept audit entries (edit outcomes) separately
    from the raw patches.
    
    Args:
        run_id: Workflow run ID
        audit_entries: List of EditAuditEntry.to_dict() results
        patches: Optional list of applied patch dicts (raw patches)
    
    Returns:
        {
            "audit": ArtifactRef.to_dict(),  # Audit entries (edit outcomes)
            "patches": ArtifactRef.to_dict() | None,  # Raw applied patches
            "count": int,
            "schema_version": "1.0"
        }
    """
    store = get_artifact_store()
    
    # Store audit entries (outcomes, validation results, etc.)
    audit_ref = store.put(
        run_id,
        "human_edits_audit",
        audit_entries,
        codec=QUALITY_ARTIFACT_CODEC,
    )
    
    result = {
        "audit": audit_ref.to_dict(),
        "count": len(audit_entries),
        "schema_version": QUALITY_SCHEMA_VERSION,
    }
    
    # Optionally store raw patches separately
    if patches:
        patches_ref = store.put(
            run_id,
            "human_edits_patches",
            patches,
            codec=QUALITY_ARTIFACT_CODEC,
        )
        result["patches"] = patches_ref.to_dict()
    
    logger.debug(
        f"Stored {len(audit_entries)} human edit audit entries, "
        f"ref={audit_ref.sha256[:8]}..."
    )
    
    return result


def store_quality_score_artifact(
    run_id: str,
    breakdown: QualityScoreBreakdown,
) -> Dict[str, Any]:
    """
    Store quality score breakdown.
    
    Returns:
        {
            "breakdown": ArtifactRef.to_dict(),
            "overall": float,  # Quick access without loading artifact
            "schema_version": "1.0"
        }
    """
    store = get_artifact_store()
    
    breakdown_ref = store.put(
        run_id,
        "quality_score",
        breakdown.to_dict(),
        codec=QUALITY_ARTIFACT_CODEC,
    )
    
    return {
        "breakdown": breakdown_ref.to_dict(),
        "overall": breakdown.overall,
        "schema_version": QUALITY_SCHEMA_VERSION,
    }


def store_regeneration_targets_artifact(
    run_id: str,
    targets: List[RegenerationTarget],
) -> Dict[str, Any]:
    """
    Store regeneration targets.
    
    Returns:
        {
            "targets": ArtifactRef.to_dict(),
            "summary": {...},  # Bounded summary
            "schema_version": "1.0"
        }
    """
    store = get_artifact_store()
    
    targets_ref = store.put(
        run_id,
        "regeneration_targets",
        [t.to_dict() for t in targets],
        codec=QUALITY_ARTIFACT_CODEC,
    )
    
    return {
        "targets": targets_ref.to_dict(),
        "summary": build_targets_summary(targets),
        "schema_version": QUALITY_SCHEMA_VERSION,
    }


# =============================================================================
# Loading Functions
# =============================================================================

def load_static_analysis_result(
    quality_refs: Dict[str, Any],
) -> Optional[StaticAnalysisResult]:
    """
    Load full static analysis result from artifact store.
    
    Args:
        quality_refs: The quality_refs dict from state
        
    Returns:
        StaticAnalysisResult or None if not found/error
    """
    static_refs = quality_refs.get("static")
    if not static_refs:
        return None
    
    result_ref_dict = static_refs.get("result")
    if not result_ref_dict or not ArtifactRef.is_artifact_ref(result_ref_dict):
        return None
    
    try:
        store = get_artifact_store()
        ref = ArtifactRef.from_dict(result_ref_dict)
        data = store.get(ref)
        return StaticAnalysisResult.from_dict(data)
    except Exception as e:
        logger.warning(f"Failed to load static analysis result: {e}")
        return None


def load_failure_attributions(
    quality_refs: Dict[str, Any],
) -> Optional[List[FailureAttribution]]:
    """
    Load failure attributions from artifact store.
    """
    attr_refs = quality_refs.get("sandbox_attribution")
    if not attr_refs:
        return None
    
    failures_ref_dict = attr_refs.get("failures")
    if not failures_ref_dict or not ArtifactRef.is_artifact_ref(failures_ref_dict):
        return None
    
    try:
        store = get_artifact_store()
        ref = ArtifactRef.from_dict(failures_ref_dict)
        data = store.get(ref)
        return [FailureAttribution.from_dict(d) for d in data]
    except Exception as e:
        logger.warning(f"Failed to load failure attributions: {e}")
        return None


def load_human_edits(
    quality_refs: Dict[str, Any],
) -> Optional[List[HumanEditPatch]]:
    """
    Load human edit patches from artifact store.
    """
    edits_refs = quality_refs.get("human_edits")
    if not edits_refs:
        return None
    
    audit_ref_dict = edits_refs.get("audit")
    if not audit_ref_dict or not ArtifactRef.is_artifact_ref(audit_ref_dict):
        return None
    
    try:
        store = get_artifact_store()
        ref = ArtifactRef.from_dict(audit_ref_dict)
        data = store.get(ref)
        return [HumanEditPatch.from_dict(d) for d in data]
    except Exception as e:
        logger.warning(f"Failed to load human edits: {e}")
        return None


# =============================================================================
# Bounded Summary Builders
# =============================================================================

def build_static_analysis_summary(result: StaticAnalysisResult) -> Dict[str, Any]:
    """
    Build bounded summary for static analysis result.
    
    Enforces MAX_ISSUES_IN_SUMMARY to keep payload small.
    Includes total counts so UI can show "showing 10 of 200 issues".
    """
    # Sort issues: errors first, then warnings, then info
    severity_order = {"error": 0, "warning": 1, "info": 2}
    sorted_issues = sorted(
        result.issues,
        key=lambda i: (severity_order.get(i.severity, 3), i.file_path, i.line_number)
    )
    
    # Take bounded subset
    preview_issues = sorted_issues[:MAX_ISSUES_IN_SUMMARY]
    
    return {
        "passed": result.passed,
        "blocking_count": result.blocking_count,
        "warning_count": result.warning_count,
        "total_issues": len(result.issues),
        "issues_truncated": len(result.issues) > MAX_ISSUES_IN_SUMMARY,
        "issues_preview": [
            {
                "severity": i.severity,
                "file": _truncate_path(i.file_path),
                "line": i.line_number,
                "message": _truncate(i.message, MAX_ERROR_MESSAGE_LENGTH),
                "rule": i.rule_id,
            }
            for i in preview_issues
        ],
        "tool_versions": result.tool_versions,
    }


def build_attribution_summary(attributions: List[FailureAttribution]) -> Dict[str, Any]:
    """
    Build bounded summary for failure attributions.
    
    Highlights high-confidence attributions for targeted regeneration.
    """
    high_confidence = [a for a in attributions if a.confidence >= 0.7]
    low_confidence = [a for a in attributions if a.confidence < 0.7]
    
    # Preview: high confidence first, then low confidence
    preview = (high_confidence + low_confidence)[:MAX_ISSUES_IN_SUMMARY]
    
    return {
        "count": len(attributions),
        "high_confidence_count": len(high_confidence),
        "attributions_truncated": len(attributions) > MAX_ISSUES_IN_SUMMARY,
        "attributions_preview": [
            {
                "test": a.test_name,
                "error_type": a.error_type,
                "likely_file": _truncate_path(a.likely_cause_file) if a.likely_cause_file else None,
                "confidence": round(a.confidence, 2),
                "hints": [_truncate(h, MAX_HINT_LENGTH) for h in a.fix_hints[:MAX_FIX_HINTS]],
            }
            for a in preview
        ],
    }


def build_targets_summary(targets: List[RegenerationTarget]) -> Dict[str, Any]:
    """
    Build bounded summary for regeneration targets.
    """
    # Sort by priority (highest first)
    sorted_targets = sorted(targets, key=lambda t: -t.priority)
    preview = sorted_targets[:MAX_TARGETS]
    
    return {
        "count": len(targets),
        "targets_truncated": len(targets) > MAX_TARGETS,
        "targets_preview": [
            {
                "file": _truncate_path(t.file_path),
                "reason": _truncate(t.reason, MAX_HINT_LENGTH),
                "priority": t.priority,
                "failures": len(t.failures_linked),
            }
            for t in preview
        ],
    }


def build_quality_score_summary(breakdown: QualityScoreBreakdown) -> Dict[str, Any]:
    """
    Build summary for quality score (already small, so just include all).
    """
    return {
        "overall": round(breakdown.overall, 1),
        "static_analysis": round(breakdown.static_analysis, 1),
        "test_coverage": round(breakdown.test_coverage, 1) if breakdown.test_coverage is not None else None,
        "complexity": round(breakdown.complexity, 1) if breakdown.complexity is not None else None,
    }


# =============================================================================
# Utility Functions
# =============================================================================

def _truncate(text: str, max_length: int) -> str:
    """Truncate text with ellipsis if too long."""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


def _truncate_path(path: Optional[str]) -> Optional[str]:
    """Truncate file path to MAX_FILE_PATH_LENGTH."""
    if path is None:
        return None
    return _truncate(path, MAX_FILE_PATH_LENGTH)


def refs_to_dicts(refs: Dict[str, ArtifactRef]) -> Dict[str, Dict[str, Any]]:
    """
    Convert a dict of ArtifactRefs to a dict of dicts for JSON serialization.
    """
    return {k: v.to_dict() for k, v in refs.items()}


def validate_quality_refs_size(quality_refs: Dict[str, Any], max_bytes: int = 65536) -> bool:
    """
    Validate that quality_refs dict stays under size threshold.
    
    Args:
        quality_refs: The quality_refs dict from state
        max_bytes: Maximum allowed size (default 64KB)
        
    Returns:
        True if size is acceptable, False otherwise
    """
    import json
    try:
        serialized = json.dumps(quality_refs, default=str)
        size = len(serialized.encode('utf-8'))
        if size > max_bytes:
            logger.warning(
                f"quality_refs size {size} bytes exceeds threshold {max_bytes}"
            )
            return False
        return True
    except Exception as e:
        logger.warning(f"Failed to validate quality_refs size: {e}")
        return False


def check_no_blobs_in_quality_refs(quality_refs: Dict[str, Any]) -> List[str]:
    """
    Check that quality_refs contains no large blobs.
    
    Returns list of violations (empty if clean).
    """
    violations = []
    
    def _check_dict(d: Dict[str, Any], path: str = "") -> None:
        for key, value in d.items():
            current_path = f"{path}.{key}" if path else key
            
            # Check for known blob indicators
            if key == "content" and isinstance(value, str) and len(value) > 1000:
                violations.append(f"{current_path}: contains 'content' blob ({len(value)} chars)")
            
            if key == "issues" and isinstance(value, list) and len(value) > MAX_ISSUES_IN_SUMMARY:
                violations.append(f"{current_path}: issues list not bounded ({len(value)} items)")
            
            if isinstance(value, str) and len(value) > 10000:
                violations.append(f"{current_path}: large string ({len(value)} chars)")
            
            if isinstance(value, list) and len(value) > 100:
                # Could be a blob
                violations.append(f"{current_path}: large list ({len(value)} items)")
            
            if isinstance(value, dict):
                # Recurse, but skip artifact refs (they're expected to have some fields)
                if not ArtifactRef.is_artifact_ref(value):
                    _check_dict(value, current_path)
    
    _check_dict(quality_refs)
    return violations
