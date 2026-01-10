"""
Review artifact storage utilities.

Stores review-related data externally and returns ArtifactRefs.
Enforces refs-not-blobs discipline for interrupt payloads.

Per ADR-HITL-ENHANCEMENT-v2:
- interrupt() payloads must be < 2KB (refs + bounded summaries only)
- Large data (diffs, sandbox results, code snapshots) goes to artifact store
- JSON codec only for review artifacts (no pickle for security/portability)

CRITICAL INVARIANTS:
1. REVIEW_ARTIFACT_CODEC = JSON only (no pickle)
2. Unified diff patches stored as text (list of strings), NOT before/after blobs
3. All refs must roundtrip: from_dict(ref.to_dict()) == ref
4. Payload summaries are bounded (max 10 files, max 200 chars per error preview)
"""

import difflib
import hashlib
import logging
from typing import Any, Dict, List, Optional

from integration_coworker.persistence.artifacts.fs import get_artifact_store
from integration_coworker.persistence.artifacts.base import ArtifactRef, ArtifactCodec

logger = logging.getLogger(__name__)

# =============================================================================
# Constants - Bounded summaries to keep payload small
# =============================================================================

# INVARIANT: Review artifacts are JSON-only. No pickle for safety and portability.
REVIEW_ARTIFACT_CODEC = ArtifactCodec.JSON

# Import bounded constants from single source of truth
from integration_coworker.graph.production_guardrails import (
    MAX_FILES_IN_SUMMARY,
    MAX_ERROR_PREVIEWS,
    MAX_ERROR_PREVIEW_CHARS,
    MAX_FILE_PATH_LENGTH as MAX_FILE_PATH_CHARS,  # Alias for backwards compat
)


# =============================================================================
# Storage Functions
# =============================================================================

def store_review_artifacts(
    run_id: str,
    kind: str,  # "pre_write" or "post_sandbox"
    *,
    code_artifacts: Optional[List] = None,
    sandbox_result: Optional[Dict[str, Any]] = None,
    diff_patches: Optional[List[str]] = None,
) -> Dict[str, ArtifactRef]:
    """
    Store all review artifacts and return refs.
    
    CRITICAL: Uses JSON codec only. Pickle is forbidden for review artifacts
    due to security concerns and portability requirements.
    
    Args:
        run_id: The workflow run ID
        kind: "pre_write" or "post_sandbox"
        code_artifacts: List of CodeArtifact objects (optional)
        sandbox_result: Sandbox execution result dict (optional)
        diff_patches: List of unified diff strings (optional)
        
    Returns:
        Dict mapping artifact type to ArtifactRef:
        {
            "code_snapshot": ArtifactRef,      # Metadata about code artifacts
            "sandbox_result": ArtifactRef,     # Full sandbox result (if post_sandbox)
            "diff_patches": ArtifactRef,       # Unified diff text patches
        }
    """
    store = get_artifact_store()
    refs: Dict[str, ArtifactRef] = {}
    
    if code_artifacts:
        # Store code artifact metadata (not full content - content lives in artifacts themselves)
        snapshot = [
            {
                "path": a.rel_path[:MAX_FILE_PATH_CHARS],
                "size": len(a.content) if hasattr(a, 'content') else 0,
                "sha256": _sha256(a.content)[:16] if hasattr(a, 'content') else "",
                "type": getattr(a, 'artifact_type', 'unknown'),
            }
            for a in code_artifacts
        ]
        refs["code_snapshot"] = store.put(
            run_id,
            f"review_{kind}_snapshot",
            snapshot,
            codec=REVIEW_ARTIFACT_CODEC,
        )
        logger.debug(f"Stored code snapshot artifact: {len(snapshot)} files")
    
    if sandbox_result:
        # Store full sandbox result externally
        refs["sandbox_result"] = store.put(
            run_id,
            f"review_{kind}_sandbox",
            sandbox_result,
            codec=REVIEW_ARTIFACT_CODEC,
        )
        logger.debug(f"Stored sandbox result artifact")
    
    if diff_patches:
        # Store unified diff patches (text), NOT before/after blobs
        # This is critical for refs-not-blobs discipline
        refs["diff_patches"] = store.put(
            run_id,
            f"review_{kind}_diff_patches",
            diff_patches,
            codec=REVIEW_ARTIFACT_CODEC,
        )
        logger.debug(f"Stored diff patches artifact: {len(diff_patches)} patches")
    
    return refs


def compute_unified_diff_patches(
    code_artifacts: List,
    existing_files: Dict[str, str],
) -> List[str]:
    """
    Generate unified diff patches for changed files.
    
    Returns list of unified diff strings (one per file).
    These are TEXT patches, not structured before/after blobs.
    
    Args:
        code_artifacts: List of CodeArtifact objects with rel_path and content
        existing_files: Dict mapping rel_path to existing file content
        
    Returns:
        List of unified diff strings
    """
    patches = []
    for artifact in code_artifacts:
        if not hasattr(artifact, 'rel_path') or not hasattr(artifact, 'content'):
            continue
            
        before = existing_files.get(artifact.rel_path, "")
        after = artifact.content
        
        if before != after:
            patch = "".join(difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{artifact.rel_path}",
                tofile=f"b/{artifact.rel_path}",
            ))
            if patch:  # Only add non-empty patches
                patches.append(patch)
                
    return patches


def load_review_artifact(ref: ArtifactRef) -> Any:
    """
    Load an artifact by reference.
    
    Args:
        ref: ArtifactRef pointing to the artifact
        
    Returns:
        Deserialized artifact content
    """
    store = get_artifact_store()
    return store.get(ref)


def load_review_artifact_from_dict(ref_dict: Dict[str, Any]) -> Any:
    """
    Load an artifact from a serialized ref dict.
    
    Convenience function for UI/CLI that receives refs as dicts.
    
    Args:
        ref_dict: Dict from ArtifactRef.to_dict()
        
    Returns:
        Deserialized artifact content
    """
    ref = ArtifactRef.from_dict(ref_dict)
    return load_review_artifact(ref)


# =============================================================================
# Payload Building - Bounded Summaries
# =============================================================================

def build_code_summary(code_artifacts: Optional[List]) -> Dict[str, Any]:
    """
    Build bounded summary of code artifacts for interrupt payload.
    
    Summary stays small (< 1KB) by limiting file count and truncating paths.
    """
    if not code_artifacts:
        return {
            "file_count": 0,
            "adds": 0,
            "total_bytes": 0,
            "files": [],
            "files_truncated": False,
        }
    
    total_bytes = sum(
        len(a.content) if hasattr(a, 'content') else 0 
        for a in code_artifacts
    )
    
    # Bounded file list - only first N files
    files = []
    for a in code_artifacts[:MAX_FILES_IN_SUMMARY]:
        files.append({
            "path": a.rel_path[:MAX_FILE_PATH_CHARS] if hasattr(a, 'rel_path') else "unknown",
            "bytes": len(a.content) if hasattr(a, 'content') else 0,
        })
    
    return {
        "file_count": len(code_artifacts),
        "adds": sum(1 for a in code_artifacts if getattr(a, 'artifact_type', '') == 'client_code'),
        "total_bytes": total_bytes,
        "files": files,
        "files_truncated": len(code_artifacts) > MAX_FILES_IN_SUMMARY,
    }


def build_sandbox_summary(sandbox_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build bounded summary of sandbox result for interrupt payload.
    
    Summary stays small (< 1KB) by limiting error previews and truncating messages.
    """
    if not sandbox_result:
        return {
            "all_passed": True,
            "gates_failed": [],
            "error_previews": [],
        }
    
    # Extract gate results
    gate_results = sandbox_result.get("gate_results", [])
    failed_gates = [
        g.get("gate", "unknown") 
        for g in gate_results 
        if not g.get("passed", True)
    ]
    
    # Extract errors with bounded previews
    errors = sandbox_result.get("errors", [])
    error_previews = []
    for err in errors[:MAX_ERROR_PREVIEWS]:
        if isinstance(err, dict):
            preview = {
                "gate": err.get("gate", "unknown"),
                "message": err.get("message", str(err))[:MAX_ERROR_PREVIEW_CHARS],
            }
        else:
            preview = {
                "gate": "unknown",
                "message": str(err)[:MAX_ERROR_PREVIEW_CHARS],
            }
        error_previews.append(preview)
    
    return {
        "all_passed": sandbox_result.get("all_gates_passed", len(failed_gates) == 0),
        "gates_failed": failed_gates[:5],  # Max 5 gate names
        "error_previews": error_previews,
    }


# =============================================================================
# Helpers
# =============================================================================

def _sha256(content: str) -> str:
    """Compute SHA256 hash of content."""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def refs_to_dicts(refs: Dict[str, ArtifactRef]) -> Dict[str, Dict[str, Any]]:
    """
    Convert ArtifactRef dict to serializable dict of dicts.
    
    Use this when building interrupt payloads.
    """
    return {k: v.to_dict() for k, v in refs.items()}


def dicts_to_refs(ref_dicts: Dict[str, Dict[str, Any]]) -> Dict[str, ArtifactRef]:
    """
    Convert serialized ref dicts back to ArtifactRef objects.
    
    Use this when processing resume decisions.
    """
    return {k: ArtifactRef.from_dict(v) for k, v in ref_dicts.items()}
