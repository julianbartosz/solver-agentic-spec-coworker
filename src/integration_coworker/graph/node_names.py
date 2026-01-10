"""
Canonical Node Names - Single source of truth for all graph node identifiers.

Per ADR-CONTRACT-UNIFICATION: Centralize all node name constants to prevent
runtime contract drift. All graph ordering, dependencies, and exemption sets
must import from here - no string literals.

Usage:
    from integration_coworker.graph.node_names import (
        PLAN_RUN, INGEST_SPEC, CODE_REVIEW_GATE,
        WORKFLOW_NODE_ORDER, NODE_DEPENDENCIES,
    )
"""
from typing import Dict, FrozenSet, List, Tuple

# =============================================================================
# Phase 1: Spec Ingestion
# =============================================================================

PLAN_RUN = "plan_run"
INGEST_SPEC = "ingest_spec"
DETECT_AND_PARSE_SPEC = "detect_and_parse_spec"
BUILD_SILVER_API_MODEL = "build_silver_api_model"
BUILD_SILVER_FILE_MODEL = "build_silver_file_model"
EMBED_SPEC_CHUNKS = "embed_spec_chunks"
PERSIST_SILVER_CHECKPOINT = "persist_silver_checkpoint"

# =============================================================================
# Phase 2: Task Understanding
# =============================================================================

UNDERSTAND_TASK = "understand_task"
ALIGN_TASK_WITH_KG = "align_task_with_kg"
PLAN_INTEGRATION_FLOW = "plan_integration_flow"
ATTACH_POLICIES_AND_PATTERNS = "attach_policies_and_patterns"

# =============================================================================
# Phase 3: Code Generation
# =============================================================================

GENERATE_CODE_AND_TESTS = "generate_code_and_tests"
PERSIST_GOLD_CHECKPOINT = "persist_gold_checkpoint"
PERSIST_KG_LEARNING = "persist_kg_learning"

# =============================================================================
# Phase 4: Repo Integration (optional)
# =============================================================================

ATTACH_REPO_CONTEXT = "attach_repo_context"
ANALYZE_REPO_LAYOUT = "analyze_repo_layout"
CODE_REVIEW_GATE = "code_review_gate"  # Canonical name (was hitl_review_gate)
APPLY_REPO_INTEGRATION_CHANGES = "apply_repo_integration_changes"

# =============================================================================
# Phase 5: Validation & Sandbox
# =============================================================================

VALIDATE_INTEGRATION_DESIGN = "validate_integration_design"
SANDBOX_GATE = "sandbox_gate"
SANDBOX_ATTRIBUTION_GATE = "sandbox_attribution_gate"
SANDBOX_REVIEW_GATE = "sandbox_review_gate"  # HITL gate for sandbox issues
STATIC_ANALYSIS_GATE = "static_analysis_gate"

# =============================================================================
# Phase 6: Reporting
# =============================================================================

BUILD_REPORT = "build_report"
PERSIST_RUN_OUTCOME = "persist_run_outcome"

# =============================================================================
# Error Handling
# =============================================================================

HANDLE_ERROR = "handle_error"

# =============================================================================
# Derived Constants
# =============================================================================

# HITL-exempt nodes: do not apply timeout/budget enforcement (they use interrupt())
HITL_EXEMPT_NODES: FrozenSet[str] = frozenset({
    CODE_REVIEW_GATE,
    SANDBOX_REVIEW_GATE,
})

# Ordered list of all node names in the workflow for recovery
WORKFLOW_NODE_ORDER: Tuple[str, ...] = (
    PLAN_RUN,
    INGEST_SPEC,
    DETECT_AND_PARSE_SPEC,
    BUILD_SILVER_API_MODEL,
    BUILD_SILVER_FILE_MODEL,
    EMBED_SPEC_CHUNKS,
    PERSIST_SILVER_CHECKPOINT,
    UNDERSTAND_TASK,
    ALIGN_TASK_WITH_KG,
    PLAN_INTEGRATION_FLOW,
    ATTACH_POLICIES_AND_PATTERNS,
    GENERATE_CODE_AND_TESTS,
    PERSIST_GOLD_CHECKPOINT,
    PERSIST_KG_LEARNING,
    # Optional repo nodes (may be skipped)
    ATTACH_REPO_CONTEXT,
    ANALYZE_REPO_LAYOUT,
    CODE_REVIEW_GATE,
    APPLY_REPO_INTEGRATION_CHANGES,
    # Final nodes
    VALIDATE_INTEGRATION_DESIGN,
    BUILD_REPORT,
    PERSIST_RUN_OUTCOME,
)

# Dependency graph: which nodes must complete before each node can run
NODE_DEPENDENCIES: Dict[str, Tuple[str, ...]] = {
    # Phase 1: Spec ingestion (linear chain)
    PLAN_RUN: (),
    INGEST_SPEC: (PLAN_RUN,),
    DETECT_AND_PARSE_SPEC: (INGEST_SPEC,),
    BUILD_SILVER_API_MODEL: (DETECT_AND_PARSE_SPEC,),
    BUILD_SILVER_FILE_MODEL: (BUILD_SILVER_API_MODEL,),
    EMBED_SPEC_CHUNKS: (BUILD_SILVER_FILE_MODEL,),
    PERSIST_SILVER_CHECKPOINT: (EMBED_SPEC_CHUNKS,),
    
    # Phase 2: Task understanding (depends on Silver model)
    UNDERSTAND_TASK: (PERSIST_SILVER_CHECKPOINT,),
    ALIGN_TASK_WITH_KG: (UNDERSTAND_TASK,),
    PLAN_INTEGRATION_FLOW: (ALIGN_TASK_WITH_KG,),
    ATTACH_POLICIES_AND_PATTERNS: (PLAN_INTEGRATION_FLOW,),
    
    # Phase 3: Code generation (depends on flow)
    GENERATE_CODE_AND_TESTS: (ATTACH_POLICIES_AND_PATTERNS,),
    PERSIST_GOLD_CHECKPOINT: (GENERATE_CODE_AND_TESTS,),
    PERSIST_KG_LEARNING: (PERSIST_GOLD_CHECKPOINT,),
    
    # Phase 4: Repo integration (optional, conditional)
    ATTACH_REPO_CONTEXT: (PERSIST_KG_LEARNING,),  # Only if use_repo
    ANALYZE_REPO_LAYOUT: (ATTACH_REPO_CONTEXT,),
    CODE_REVIEW_GATE: (ANALYZE_REPO_LAYOUT,),
    APPLY_REPO_INTEGRATION_CHANGES: (CODE_REVIEW_GATE,),
    
    # Phase 5: Validation and reporting
    VALIDATE_INTEGRATION_DESIGN: (PERSIST_KG_LEARNING,),  # OR apply_repo_integration_changes
    BUILD_REPORT: (VALIDATE_INTEGRATION_DESIGN,),
    PERSIST_RUN_OUTCOME: (BUILD_REPORT,),
    
    # Error handling
    HANDLE_ERROR: (),  # Can run anytime
}

# Category mapping for telemetry and logging
NODE_CATEGORY_MAP: Dict[str, str] = {
    # Ingestion nodes
    PLAN_RUN: "ingestion",
    INGEST_SPEC: "ingestion",
    DETECT_AND_PARSE_SPEC: "ingestion",
    BUILD_SILVER_API_MODEL: "ingestion",
    BUILD_SILVER_FILE_MODEL: "ingestion",
    EMBED_SPEC_CHUNKS: "ingestion",
    PERSIST_SILVER_CHECKPOINT: "persistence",
    
    # Reasoning nodes
    UNDERSTAND_TASK: "reasoning",
    ALIGN_TASK_WITH_KG: "reasoning",
    PLAN_INTEGRATION_FLOW: "reasoning",
    ATTACH_POLICIES_AND_PATTERNS: "reasoning",
    
    # Generation nodes
    GENERATE_CODE_AND_TESTS: "generation",
    PERSIST_GOLD_CHECKPOINT: "persistence",
    PERSIST_KG_LEARNING: "persistence",
    
    # Repo nodes
    ATTACH_REPO_CONTEXT: "repo",
    ANALYZE_REPO_LAYOUT: "repo",
    CODE_REVIEW_GATE: "hitl",
    APPLY_REPO_INTEGRATION_CHANGES: "repo",
    
    # Validation nodes
    VALIDATE_INTEGRATION_DESIGN: "validation",
    SANDBOX_GATE: "validation",
    SANDBOX_ATTRIBUTION_GATE: "validation",
    SANDBOX_REVIEW_GATE: "hitl",
    STATIC_ANALYSIS_GATE: "validation",
    
    # Reporting nodes
    BUILD_REPORT: "reporting",
    PERSIST_RUN_OUTCOME: "persistence",
    
    # Error handling
    HANDLE_ERROR: "error",
}


def get_node_names() -> List[str]:
    """Get the list of workflow node names in execution order."""
    return list(WORKFLOW_NODE_ORDER)


def get_dependent_nodes(node_name: str) -> List[str]:
    """
    Get all nodes that transitively depend on the given node.
    
    If node X is skipped, all nodes returned by this function must also be skipped.
    
    Args:
        node_name: The node being skipped
        
    Returns:
        List of node names that depend on the skipped node (in execution order)
    """
    dependents: List[str] = []
    
    for name, deps in NODE_DEPENDENCIES.items():
        if node_name in deps:
            dependents.append(name)
            # Recursively get nodes that depend on this dependent
            dependents.extend(get_dependent_nodes(name))
    
    # Remove duplicates while preserving order
    seen = set()
    result: List[str] = []
    for name in dependents:
        if name not in seen:
            seen.add(name)
            result.append(name)
    
    return result


def is_hitl_exempt(node_name: str) -> bool:
    """Check if a node is exempt from timeout/budget enforcement."""
    return node_name in HITL_EXEMPT_NODES


def get_node_category(node_name: str) -> str:
    """Get the category of a node for telemetry."""
    return NODE_CATEGORY_MAP.get(node_name, "unknown")
