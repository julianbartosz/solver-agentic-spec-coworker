"""
Node Contract Helpers - Unified state access patterns for graph nodes.

Per ADR-CONTRACT-UNIFICATION: LangGraph nodes return partial state updates as dicts.
This module provides type-safe helpers for:
1. Reading from input state (always WorkflowState)
2. Validating node return types
3. Merging partial updates into WorkflowState

The key insight is that LangGraph nodes return UPDATES (typically dicts), not the full state.
LangGraph merges these updates internally. For checkpointing, we need the POST-MERGE state,
not the input state. This module provides `normalize_node_result` to compute both.

Usage:
    from integration_coworker.graph.node_contract import (
        state_attr, state_optional, validate_node_result, normalize_node_result,
        NodeResultType,
    )

    def my_node(state: WorkflowState) -> Dict[str, Any]:
        run_id = state_attr(state, 'run_id')
        optional_field = state_optional(state, 'sandbox_result', default=None)
        return {"completed_steps": state.completed_steps + ["my_node"]}
"""
from dataclasses import replace, fields, is_dataclass
from typing import Any, Dict, Mapping, Optional, Tuple, TypeVar, Union, overload

from integration_coworker.graph.state import WorkflowState

# Type for node return values: dict (partial update), WorkflowState (full), or None (no change)
NodeResultType = Union[Dict[str, Any], WorkflowState, None]

T = TypeVar("T")


def state_attr(state: WorkflowState, name: str) -> Any:
    """
    Get a required attribute from WorkflowState.
    
    Raises AttributeError if the attribute doesn't exist or is None.
    Use this for attributes that must be present for the node to function.
    
    Args:
        state: The input WorkflowState
        name: Attribute name
        
    Returns:
        The attribute value
        
    Raises:
        AttributeError: If attribute is missing or None
    """
    value = getattr(state, name, None)
    if value is None:
        raise AttributeError(f"Required state attribute '{name}' is missing or None")
    return value


@overload
def state_optional(state: WorkflowState, name: str) -> Optional[Any]: ...

@overload
def state_optional(state: WorkflowState, name: str, default: T) -> Union[Any, T]: ...

def state_optional(state: WorkflowState, name: str, default: Any = None) -> Any:
    """
    Get an optional attribute from WorkflowState.
    
    Returns default if the attribute doesn't exist or is None.
    Use this for attributes that may or may not be present.
    
    Args:
        state: The input WorkflowState
        name: Attribute name
        default: Value to return if attribute is missing (default: None)
        
    Returns:
        The attribute value or default
    """
    value = getattr(state, name, None)
    return value if value is not None else default


def validate_node_result(result: Any, node_name: str) -> NodeResultType:
    """
    Validate that a node result conforms to the contract.
    
    Valid return types:
    - Dict[str, Any]: Partial state update (most common)
    - WorkflowState: Full state replacement (discouraged but supported)
    - None: No state change
    
    Args:
        result: The value returned by the node function
        node_name: Name of the node (for error messages)
        
    Returns:
        The validated result
        
    Raises:
        TypeError: If result is not a valid type
    """
    if result is None:
        return None
    
    if isinstance(result, WorkflowState):
        return result
    
    if isinstance(result, Mapping):
        # Convert to dict if it's another Mapping type
        return dict(result) if not isinstance(result, dict) else result
    
    raise TypeError(
        f"Node '{node_name}' returned invalid type {type(result).__name__}. "
        f"Expected Dict[str, Any], WorkflowState, or None. "
        f"Got: {repr(result)[:100]}"
    )


def is_partial_update(result: NodeResultType) -> bool:
    """Check if a node result is a partial state update (dict)."""
    return isinstance(result, dict)


def is_full_state(result: NodeResultType) -> bool:
    """Check if a node result is a full WorkflowState."""
    return isinstance(result, WorkflowState)


def normalize_node_result(
    state: WorkflowState,
    result: NodeResultType,
    node_name: str = "unknown",
) -> Tuple[WorkflowState, Dict[str, Any]]:
    """
    Normalize a node result into (merged_state, update_dict).
    
    LangGraph nodes return partial updates (dicts), but checkpointing and metrics
    need the POST-MERGE state. This function computes both:
    
    - merged_state: The state after applying the node's update (for checkpointing)
    - update_dict: The dict to return to LangGraph for its internal merge
    
    Args:
        state: The input WorkflowState passed to the node
        result: The validated node result (dict, WorkflowState, or None)
        node_name: Name of the node (for error messages)
        
    Returns:
        Tuple of (merged_state, update_dict):
        - merged_state: WorkflowState with update applied
        - update_dict: The partial update dict (empty if full state or None returned)
        
    Raises:
        TypeError: If result contains unknown state keys (typo detection)
    """
    # None means no change
    if result is None:
        return state, {}
    
    # Full WorkflowState returned - use it directly, no update dict
    if isinstance(result, WorkflowState):
        return result, {}
    
    # Dict (partial update) - validate keys and merge into state
    if isinstance(result, Mapping):
        update = dict(result)
        
        # Fail fast on unknown keys - catches typos like "compelted_steps"
        valid_field_names = {f.name for f in fields(state)}
        unknown = [k for k in update.keys() if k not in valid_field_names]
        if unknown:
            raise TypeError(
                f"Node '{node_name}' returned unknown state keys: {unknown}. "
                f"Valid keys are: {sorted(valid_field_names)}"
            )
        
        # Merge update into state using dataclass replace()
        merged = replace(state, **update)
        return merged, update
    
    # Should not reach here if validate_node_result was called first
    raise TypeError(f"normalize_node_result got unexpected type: {type(result)}")


def get_run_id_from_state(state: WorkflowState) -> str:
    """
    Get run_id from state, with fallback to plan dict.
    
    This is the canonical way to get run_id - always use this
    instead of accessing state.run_id directly.
    """
    run_id = getattr(state, 'run_id', None)
    if run_id:
        return run_id
    
    # Fallback to plan dict (legacy pattern)
    plan = getattr(state, 'plan', None)
    if plan and isinstance(plan, dict):
        return plan.get('run_id', '')
    
    return ''


def get_options_attr(state: WorkflowState, name: str, default: T = None) -> Union[Any, T]:
    """
    Get an attribute from state.options safely.
    
    Args:
        state: The input WorkflowState
        name: Attribute name on the options object
        default: Value to return if options is None or attribute missing
        
    Returns:
        The options attribute value or default
    """
    options = getattr(state, 'options', None)
    if options is None:
        return default
    return getattr(options, name, default)
