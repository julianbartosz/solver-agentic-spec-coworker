"""
Sandbox Attribution Gate Node (PR #9)

Graph node that runs failure attribution after sandbox execution.
Stores results in artifact store, updates state with bounded refs.

Per ADR-QUALITY-SIGNALS:
- Pure signal extraction (no LLM calls)
- Bounded artifact storage (refs-not-blobs)
- Deterministic for replay (sorted outputs, stable serialization)

This node is inserted after sandbox_gate and before review_gate.
"""
import json
import logging
from typing import Any, Dict, List

from integration_coworker.graph.sandbox_attribution import (
    AttributionResult,
    FailureCategory,
    attribute_gate_results,
    summarize_attributions,
    is_code_failure,
)
from integration_coworker.graph.quality_models import FailureAttribution
from integration_coworker.persistence.artifacts import ArtifactRef, ArtifactCodec, get_artifact_store
from integration_coworker.graph.state import WorkflowState

logger = logging.getLogger(__name__)


# =============================================================================
# Constants - Import from single source of truth
# =============================================================================

from integration_coworker.graph.production_guardrails import (
    MAX_HINT_LENGTH,
    MAX_FIX_HINTS as MAX_HINTS_PER_ATTRIBUTION,
    MAX_ATTRIBUTIONS_IN_STATE,
)

# Artifact codec
ATTRIBUTION_CODEC = ArtifactCodec.JSON
ATTRIBUTION_SCHEMA_VERSION = "1.0"


# =============================================================================
# Node Function
# =============================================================================

def sandbox_attribution_gate(state: WorkflowState) -> Dict[str, Any]:
    """
    Attribute sandbox failures to categories.
    
    Reads sandbox_result from state, applies pattern matching,
    stores attributions in artifact store.
    
    Args:
        state: WorkflowState with sandbox_result attribute
        
    Returns:
        State updates:
            - sandbox_attribution_summary: bounded summary
            - quality_refs["sandbox_attribution"]: artifact refs
    """
    run_id = getattr(state, 'run_id', 'unknown')
    sandbox_result = getattr(state, 'sandbox_result', None)
    
    # Skip if no sandbox result
    if not sandbox_result:
        logger.debug(f"[{run_id}] No sandbox result, skipping attribution")
        return {
            "sandbox_attribution_summary": dict(sorted({
                "actionable": False,
                "by_category": {},
                "fingerprints": [],
                "primary_category": "unknown",
                "skip_reason": "no_sandbox_result",
                "skipped": True,
                "top_hints": [],
                "total_failures": 0,
            }.items()))
        }
    
    # Skip if sandbox passed
    if sandbox_result.get("success", False):
        logger.debug(f"[{run_id}] Sandbox passed, skipping attribution")
        return {
            "sandbox_attribution_summary": dict(sorted({
                "actionable": False,
                "by_category": {},
                "fingerprints": [],
                "primary_category": "unknown",
                "skip_reason": "sandbox_passed",
                "skipped": True,
                "top_hints": [],
                "total_failures": 0,
            }.items()))
        }
    
    # Get gate results
    gate_results = sandbox_result.get("gate_results", [])
    if not gate_results:
        logger.debug(f"[{run_id}] No gate results in sandbox result")
        return {
            "sandbox_attribution_summary": dict(sorted({
                "actionable": False,
                "by_category": {},
                "fingerprints": [],
                "primary_category": "unknown",
                "skip_reason": "no_gate_results",
                "skipped": True,
                "top_hints": [],
                "total_failures": 0,
            }.items()))
        }
    
    # Normalize gate results to dict format
    normalized_gates = _normalize_gate_results(gate_results)
    
    # Run attribution
    attributions = attribute_gate_results(normalized_gates)
    
    # Bound attributions
    bounded_attributions = attributions[:MAX_ATTRIBUTIONS_IN_STATE]
    
    # Build summary (always bounded)
    summary = summarize_attributions(bounded_attributions)
    
    # Convert to FailureAttribution for artifact storage
    failure_attributions = _convert_to_failure_attributions(
        bounded_attributions, normalized_gates
    )
    
    # Store in artifact store
    quality_refs = _store_attributions(run_id, failure_attributions, summary)
    
    logger.info(
        f"[{run_id}] Attributed {len(bounded_attributions)} failures: "
        f"categories={summary['by_category']}, actionable={summary['actionable']}"
    )
    
    return {
        "sandbox_attribution_summary": summary,
        "quality_refs": quality_refs,
    }


# =============================================================================
# Helpers
# =============================================================================

def _normalize_gate_results(gate_results: List[Any]) -> List[Dict[str, Any]]:
    """
    Normalize gate results to dict format.
    
    Handles both dataclass (GateResult) and dict formats.
    """
    normalized = []
    for gate in gate_results:
        if isinstance(gate, dict):
            normalized.append(gate)
        elif hasattr(gate, "to_dict"):
            normalized.append(gate.to_dict())
        elif hasattr(gate, "__dict__"):
            # Dataclass without to_dict
            normalized.append({
                "name": getattr(gate, "name", ""),
                "passed": getattr(gate, "passed", False),
                "output": getattr(gate, "output", ""),
                "return_code": getattr(gate, "return_code", 1),
            })
        else:
            # Unknown format, skip
            logger.warning(f"Unknown gate result format: {type(gate)}")
    return normalized


def _convert_to_failure_attributions(
    attributions: List[AttributionResult],
    gate_results: List[Dict[str, Any]],
) -> List[FailureAttribution]:
    """
    Convert AttributionResult to FailureAttribution for storage.
    
    Maps attribution data to the existing FailureAttribution schema.
    """
    failure_attributions = []
    
    # Build gate name -> output lookup
    gate_outputs = {g.get("name", ""): g.get("output", "") for g in gate_results}
    
    for i, attr in enumerate(attributions):
        # Find corresponding gate (best effort)
        gate_name = ""
        error_output = ""
        for gate in gate_results:
            if not gate.get("passed", True):
                gate_name = gate.get("name", "")
                error_output = gate.get("output", "")[:500]  # Bounded
                break
        
        fa = FailureAttribution(
            test_name=f"{attr.category.value}_{i}",
            test_file=attr.likely_cause_file or "",
            error_type=attr.category.value,
            error_message=attr.fix_hints[0] if attr.fix_hints else f"Failure in {gate_name}",
            likely_cause_file=attr.likely_cause_file,
            likely_cause_lines=attr.likely_cause_lines,
            confidence=attr.confidence,
            fix_hints=attr.fix_hints[:MAX_HINTS_PER_ATTRIBUTION],
        )
        failure_attributions.append(fa)
    
    return failure_attributions


def _store_attributions(
    run_id: str,
    attributions: List[FailureAttribution],
    summary: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Store attributions in artifact store.
    
    Returns quality_refs update with sandbox_attribution key.
    Serialization uses sort_keys=True for determinism.
    """
    store = get_artifact_store()
    
    # Sort attributions by a stable key for deterministic storage
    sorted_attrs = sorted(
        attributions,
        key=lambda a: (a.error_type, a.test_file or "", a.test_name or ""),
    )
    
    # Store full attributions (sorted for determinism)
    attr_dicts = [a.to_dict() for a in sorted_attrs]
    # Ensure each dict has sorted keys
    attr_dicts = [dict(sorted(d.items())) for d in attr_dicts]
    
    attr_ref = store.put(
        run_id,
        "sandbox_attributions",
        attr_dicts,
        codec=ATTRIBUTION_CODEC,
    )
    
    return {
        "sandbox_attribution": dict(sorted({
            "failures": attr_ref.to_dict(),
            "schema_version": ATTRIBUTION_SCHEMA_VERSION,
            "summary": summary,
        }.items()))
    }


# =============================================================================
# Routing Helpers
# =============================================================================

def has_actionable_failures(state: WorkflowState) -> bool:
    """
    Check if state has actionable code failures.
    
    Used by conditional edges to decide if regeneration is needed.
    """
    summary = getattr(state, 'sandbox_attribution_summary', {})
    return summary.get("actionable", False) if summary else False


def get_failure_categories(state: WorkflowState) -> List[str]:
    """
    Get list of failure categories from state.
    
    Returns list of category values (e.g., ["code_syntax_error", "env_timeout"])
    """
    summary = getattr(state, 'sandbox_attribution_summary', {})
    by_category = summary.get("by_category", {}) if summary else {}
    return list(by_category.keys())


def get_primary_failure_category(state: WorkflowState) -> str:
    """
    Get the most common failure category.
    
    Returns category value or "unknown" if none.
    """
    summary = getattr(state, 'sandbox_attribution_summary', {})
    by_category = summary.get("by_category", {}) if summary else {}
    return max(by_category, key=lambda k: by_category[k])
