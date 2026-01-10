"""
State Schema Generator - Single Source of Truth for WorkflowState

This module auto-generates the LangGraph-compatible TypedDict (WorkflowStateDict)
from the authoritative WorkflowState dataclass, ensuring:

1. Field parity: Both definitions always have the same fields
2. Correct reducers: No `operator.add` (causes checkpoint bloat)
3. Type safety: Annotations are preserved
4. Maintainability: Add fields to state.py only, they auto-propagate

Generated at import time to avoid any runtime cost.
"""

from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import (
    Any, 
    Callable, 
    Dict, 
    List, 
    Optional, 
    Set, 
    Tuple,
    Type,
    get_type_hints,
    get_origin,
    get_args,
)

# =============================================================================
# Reducer Functions for Parallel Execution
# =============================================================================

def merge_dicts(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge two dictionaries, with right taking precedence.
    
    Used for fields like `plan`, `persisted_ids`, `node_timings`.
    """
    if left is None:
        return right or {}
    if right is None:
        return left or {}
    result = dict(left)
    result.update(right)
    return result


def last_non_none(left: Any, right: Any) -> Any:
    """
    Take the last non-None value.
    
    Used for scalar fields that should not be combined.
    This is the SAFE DEFAULT - never causes checkpoint bloat.
    """
    return right if right is not None else left


def unique_list(left: List[Any], right: List[Any]) -> List[Any]:
    """
    Combine two lists, removing duplicates while preserving order.
    
    Used for `completed_steps`, `errors`, `warnings`, `skipped_nodes`.
    """
    if left is None:
        left = []
    if right is None:
        right = []
    seen = set()
    result = []
    for item in left + right:
        # Use repr for hashability of dict items
        key = repr(item) if isinstance(item, dict) else item
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def sum_token_usage(left: Dict[str, int], right: Dict[str, int]) -> Dict[str, int]:
    """Sum token usage dictionaries."""
    if left is None:
        left = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if right is None:
        right = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return {
        "prompt_tokens": left.get("prompt_tokens", 0) + right.get("prompt_tokens", 0),
        "completion_tokens": left.get("completion_tokens", 0) + right.get("completion_tokens", 0),
        "total_tokens": left.get("total_tokens", 0) + right.get("total_tokens", 0),
    }


# =============================================================================
# Reducer Mapping Configuration
# =============================================================================

# Fields that should use unique_list reducer (dedupe on merge)
# These are "bookkeeping" lists where order matters but duplicates don't
UNIQUE_LIST_FIELDS: Set[str] = {
    "completed_steps",
    "errors",
    "warnings",
    "skipped_nodes",
}

# Fields that should use merge_dicts reducer
# These are control/tracking dicts where both branches may add keys
MERGE_DICT_FIELDS: Set[str] = {
    "plan",
    "persisted_ids",
    "node_timings",
}

# Fields that should use sum_token_usage reducer
SUM_USAGE_FIELDS: Set[str] = {
    "llm_token_usage",
}

# Fields that MUST use unique_list for dicts (LLM fallbacks, etc.)
# These can have duplicates from parallel branches
UNIQUE_LIST_DICT_FIELDS: Set[str] = {
    "llm_fallbacks",  # Each branch may report the same fallback
}

# All other fields use last_non_none (safe default)
# This includes:
# - Scalar fields (str, int, bool, Optional[...])
# - Large immutable lists (endpoints, schemas, etc.) - set once, not modified
# - Complex objects (repo_snapshot, etc.)


def get_reducer_for_field(field_name: str, field_type: Any) -> Callable:
    """
    Determine the appropriate reducer for a field based on its name and type.
    
    Priority:
    1. Explicit field name mappings (most specific)
    2. Type-based inference (fallback)
    3. last_non_none (safe default)
    
    CRITICAL: We NEVER use operator.add, which caused the Dec 22 checkpoint bloat.
    """
    # Check explicit field mappings first
    if field_name in UNIQUE_LIST_FIELDS:
        return unique_list
    
    if field_name in MERGE_DICT_FIELDS:
        return merge_dicts
    
    if field_name in SUM_USAGE_FIELDS:
        return sum_token_usage
    
    if field_name in UNIQUE_LIST_DICT_FIELDS:
        return unique_list
    
    # Safe default: last_non_none for everything else
    # This includes:
    # - Large lists like endpoints, schemas (set once, not merged)
    # - Scalar values
    # - Optional values
    # - Complex objects
    return last_non_none


# =============================================================================
# TypedDict Generator
# =============================================================================

def generate_state_dict_annotations() -> Dict[str, Tuple[Any, Callable]]:
    """
    Generate TypedDict field annotations from WorkflowState dataclass.
    
    Returns a dict mapping field_name -> (type_annotation, reducer_function)
    that can be used to construct WorkflowStateDict.
    
    This is the SINGLE SOURCE OF TRUTH for field definitions.
    """
    # Import here to avoid circular imports
    from integration_coworker.graph.state import WorkflowState
    
    # Get type hints from the dataclass
    try:
        type_hints = get_type_hints(WorkflowState)
    except Exception:
        # Fallback: use __annotations__ directly
        type_hints = WorkflowState.__annotations__
    
    annotations = {}
    
    for field in dataclass_fields(WorkflowState):
        field_name = field.name
        field_type = type_hints.get(field_name, Any)
        
        # Convert Path to str in annotations (JSON serializable)
        if field_type == Path or (get_origin(field_type) is type(None) and Path in get_args(field_type)):
            # Optional[Path] or Path -> use original, convert at runtime
            pass
        
        # Get the appropriate reducer
        reducer = get_reducer_for_field(field_name, field_type)
        
        annotations[field_name] = (field_type, reducer)
    
    return annotations


def get_all_field_names() -> Set[str]:
    """Get all field names from WorkflowState dataclass."""
    from integration_coworker.graph.state import WorkflowState
    return {f.name for f in dataclass_fields(WorkflowState)}


def get_reducer_mapping() -> Dict[str, str]:
    """
    Get a human-readable mapping of field names to reducer names.
    
    Useful for debugging and documentation.
    """
    annotations = generate_state_dict_annotations()
    return {
        field_name: reducer.__name__
        for field_name, (_, reducer) in annotations.items()
    }


# =============================================================================
# Parity Verification
# =============================================================================

def verify_field_parity(typed_dict_class: type) -> Tuple[bool, List[str]]:
    """
    Verify that a TypedDict has the same fields as WorkflowState.
    
    Args:
        typed_dict_class: The TypedDict class to verify
        
    Returns:
        (is_valid, list_of_issues)
    """
    from integration_coworker.graph.state import WorkflowState
    
    dataclass_fields_set = {f.name for f in dataclass_fields(WorkflowState)}
    typed_dict_fields = set(typed_dict_class.__annotations__.keys())
    
    issues = []
    
    missing_from_typed_dict = dataclass_fields_set - typed_dict_fields
    extra_in_typed_dict = typed_dict_fields - dataclass_fields_set
    
    if missing_from_typed_dict:
        issues.append(f"Missing from TypedDict: {sorted(missing_from_typed_dict)}")
    
    if extra_in_typed_dict:
        issues.append(f"Extra in TypedDict (not in dataclass): {sorted(extra_in_typed_dict)}")
    
    return len(issues) == 0, issues


def verify_no_operator_add(typed_dict_class: type) -> Tuple[bool, List[str]]:
    """
    Verify that no field uses operator.add reducer.
    
    operator.add causes exponential checkpoint growth and must be avoided.
    
    Args:
        typed_dict_class: The TypedDict class to verify
        
    Returns:
        (is_valid, list_of_issues)
    """
    import operator
    from typing import Annotated, get_origin, get_args
    
    issues = []
    
    for field_name, annotation in typed_dict_class.__annotations__.items():
        if get_origin(annotation) is Annotated:
            args = get_args(annotation)
            if len(args) >= 2:
                reducer = args[1]
                if reducer is operator.add:
                    issues.append(
                        f"Field '{field_name}' uses operator.add - "
                        "this causes exponential checkpoint growth!"
                    )
    
    return len(issues) == 0, issues


# =============================================================================
# Default Value Factory
# =============================================================================

def get_default_value(field_name: str, field_type: Any) -> Any:
    """
    Get the appropriate default value for a field based on its type.
    
    Used by create_initial_state_dict().
    """
    # Handle Optional types
    origin = get_origin(field_type)
    if origin is type(None):
        return None
    
    # Get the base type for Optional[X] or List[X]
    args = get_args(field_type)
    
    # Check for Optional (Union with None)
    if origin is type(None) or (args and type(None) in args):
        # It's Optional - default to None unless it's a known list field
        if 'List' in str(field_type) or origin is list:
            return []
        return None
    
    # Check for List
    if origin is list or 'List' in str(field_type):
        return []
    
    # Check for Dict
    if origin is dict or 'Dict' in str(field_type):
        # Special case for llm_token_usage
        if field_name == "llm_token_usage":
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return {}
    
    # Check for bool
    if field_type is bool:
        return False
    
    # Check for int
    if field_type is int:
        return 0
    
    # Check for str
    if field_type is str:
        return ""
    
    # Default to None for complex types
    return None


def generate_initial_state_template() -> Dict[str, Any]:
    """
    Generate a template for initial state with all default values.
    
    This ensures create_initial_state_dict() stays in sync with the dataclass.
    """
    annotations = generate_state_dict_annotations()
    
    template = {}
    for field_name, (field_type, _) in annotations.items():
        template[field_name] = get_default_value(field_name, field_type)
    
    return template
