"""
plan_integration_flow node - Validate and finalize workflow structure, create endpoint bindings.

Uses LLM with TOON format when available to enhance endpoint binding mappings.
Uses schema-based field mapping generation for deterministic mappings.

Per design doc Section 5.5:
- Archetype: plan_integration_flow.archetype.yaml
- Provider: Anthropic (claude-3-sonnet) - complex workflow planning
- Strategy: chain_of_thought with best-of-n sampling
"""
import logging
from collections import deque
from typing import List, Tuple

from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import IntegrationFlowNode, IntegrationFlowEdge
from integration_coworker.domain.models import EndpointBinding
from integration_coworker.llm import get_llm_client_for_node
from integration_coworker.llm.toon import from_toon
from integration_coworker.codegen.field_mappings import (
    generate_field_mappings,
    merge_mappings,
)

logger = logging.getLogger(__name__)


def _validate_dag_structure(
    nodes: List[IntegrationFlowNode],
    edges: List[IntegrationFlowEdge],
) -> Tuple[bool, List[str]]:
    """
    Validate that workflow nodes and edges form a valid DAG.
    
    Checks performed:
    1. Exactly one start node
    2. At least one end node
    3. All nodes are reachable from start
    4. All nodes can reach an end node
    5. No cycles (topological sort)
    
    Returns:
        Tuple of (is_valid, list of error messages)
    """
    errors = []
    
    if not nodes:
        return False, ["No workflow nodes defined"]
    
    # Check 1: Exactly one start node
    start_nodes = [n for n in nodes if n.node_type == "start"]
    if len(start_nodes) != 1:
        errors.append(f"Flow must have exactly one start node, found {len(start_nodes)}")
    
    # Check 2: At least one end node
    end_nodes = [n for n in nodes if n.node_type == "end"]
    if len(end_nodes) < 1:
        errors.append("Flow must have at least one end node")
    
    if errors:
        # Can't proceed with graph checks without valid start/end
        return False, errors
    
    # Build adjacency maps
    node_keys = {n.node_key for n in nodes}
    outgoing = {key: [] for key in node_keys}  # node -> [successors]
    incoming = {key: [] for key in node_keys}  # node -> [predecessors]
    
    for edge in edges:
        if edge.from_node_key in node_keys and edge.to_node_key in node_keys:
            outgoing[edge.from_node_key].append(edge.to_node_key)
            incoming[edge.to_node_key].append(edge.from_node_key)
    
    start_key = start_nodes[0].node_key
    end_keys = {n.node_key for n in end_nodes}
    
    # Check 3: All nodes reachable from start (forward BFS)
    reachable_from_start = set()
    queue = deque([start_key])
    while queue:
        current = queue.popleft()
        if current in reachable_from_start:
            continue
        reachable_from_start.add(current)
        for successor in outgoing.get(current, []):
            if successor not in reachable_from_start:
                queue.append(successor)
    
    unreachable = node_keys - reachable_from_start
    for key in unreachable:
        errors.append(f"Node '{key}' is not reachable from start")
    
    # Check 4: All nodes can reach an end (reverse BFS from all end nodes)
    can_reach_end = set()
    queue = deque(end_keys)
    while queue:
        current = queue.popleft()
        if current in can_reach_end:
            continue
        can_reach_end.add(current)
        for predecessor in incoming.get(current, []):
            if predecessor not in can_reach_end:
                queue.append(predecessor)
    
    dead_ends = node_keys - can_reach_end
    for key in dead_ends:
        errors.append(f"Node '{key}' cannot reach any end node")
    
    # Check 5: No cycles (Kahn's algorithm for topological sort)
    in_degree = {key: len(incoming.get(key, [])) for key in node_keys}
    queue = deque([key for key, deg in in_degree.items() if deg == 0])
    sorted_count = 0
    
    while queue:
        current = queue.popleft()
        sorted_count += 1
        for successor in outgoing.get(current, []):
            in_degree[successor] -= 1
            if in_degree[successor] == 0:
                queue.append(successor)
    
    if sorted_count != len(node_keys):
        errors.append("Cycle detected in workflow graph")
    
    return len(errors) == 0, errors

# Node name for archetype loading
NODE_NAME = "plan_integration_flow"


def _build_binding_prompt(state: WorkflowState, api_node, endpoint) -> str:
    """
    Build TOON-formatted prompt for generating request/response mappings.
    
    Uses Token-Oriented Object Notation for ~25-35% token savings vs JSON.
    """
    endpoint_info = f"{endpoint.method} {endpoint.path}"
    if endpoint.summary:
        endpoint_info += f" - {endpoint.summary}"

    return f"""Generate request and response mappings for an API call node.

TASK: {state.task_description}
ENDPOINT: {endpoint_info}
OPERATION_ID: {endpoint.operation_id or "N/A"}

Respond in TOON format (key=value notation):

request_mapping.description=how input maps to request
request_mapping.path_params=[param1,param2]
request_mapping.query_params=[param1,param2]
request_mapping.body_fields=[field1,field2]
response_mapping.description=how response maps to output
response_mapping.extract_fields=[field1,field2]

Rules:
- List actual parameter/field names from the API spec
- Use exact TOON format, no JSON
"""


def plan_integration_flow(state: WorkflowState) -> WorkflowState:
    """
    Reads: integration_task, workflow_nodes, workflow_edges, endpoints, plan
    Writes: endpoint_bindings
    
    Contract per Appendix C.3.8:
    - Validates flow structure (start/end nodes, connectivity, positions)
    - Creates EndpointBinding scaffolds for each api_call node
    - Uses LLM to enhance binding mappings when available
    """
    if not state.workflow_nodes:
        state.errors.append("No workflow_nodes from align_task_with_kg")
        state.completed_steps.append("plan_integration_flow")
        return state

    # 1. Validate DAG structure (V2: proper cycle detection + reachability)
    is_valid, validation_errors = _validate_dag_structure(
        state.workflow_nodes,
        state.workflow_edges,
    )
    
    if not is_valid:
        for error in validation_errors:
            state.errors.append(error)
        raise ValueError(f"Invalid workflow DAG: {validation_errors[0]}")

    # Check position increases along paths (simple check for linear flows)
    node_positions = {n.node_key: n.position for n in state.workflow_nodes}
    for edge in state.workflow_edges:
        from_pos = node_positions.get(edge.from_node_key, 0)
        to_pos = node_positions.get(edge.to_node_key, 0)
        if from_pos >= to_pos:
            error = f"Position constraint violated: {edge.from_node_key}(pos={from_pos}) -> {edge.to_node_key}(pos={to_pos})"
            state.errors.append(error)
            # Warning only for M3, not fatal

    # 2. Create EndpointBinding scaffolds for api_call nodes
    api_call_nodes = [n for n in state.workflow_nodes if n.node_type == "api_call"]

    if not api_call_nodes:
        # No API calls in this flow - acceptable for some workflows
        state.completed_steps.append("plan_integration_flow")
        return state

    # Get target operations from integration_task constraints
    target_operations = []
    if state.integration_task and state.integration_task.constraints:
        target_operations = state.integration_task.constraints.get("extra", {}).get("target_operations", [])

    # Create bindings for each api_call node
    for api_node in api_call_nodes:
        # Match to an endpoint
        matched_endpoint = None

        if target_operations:
            # Try to match first target operation to this node
            target_op = target_operations[0] if target_operations else None
            if target_op:
                operation_id = target_op.get("operation_id")
                method = target_op.get("method")
                path = target_op.get("path")

                for endpoint in state.endpoints:
                    if endpoint.operation_id == operation_id or \
                       (endpoint.method == method and endpoint.path == path):
                        matched_endpoint = endpoint
                        break

        # Generate schema-based mappings first
        request_mapping = {}
        response_mapping = {}

        if matched_endpoint:
            try:
                # Generate deterministic mappings from schema
                request_mapping, response_mapping = generate_field_mappings(
                    endpoint=matched_endpoint,
                    parameters=state.endpoint_parameters,
                    schemas=state.schemas,
                    schema_fields=state.schema_fields,
                )
                logger.info(f"Generated schema-based mappings for {api_node.node_key}")
            except Exception as e:
                logger.warning(f"Schema mapping generation failed: {e}")

            # Optionally enhance with LLM
            try:
                client = get_llm_client_for_node(NODE_NAME)

                prompt = _build_binding_prompt(state, api_node, matched_endpoint)
                llm_response_text = client.complete(prompt)

                # V2: Removed string-based mock detection (LLM-006)
                # If LLM returns invalid TOON, the parse will fail and we keep schema mappings
                if llm_response_text:
                    try:
                        llm_response = from_toon(llm_response_text)
                        llm_request = llm_response.get("request_mapping", {})
                        llm_response_map = llm_response.get("response_mapping", {})

                        # Merge LLM enhancements into schema mappings
                        request_mapping = merge_mappings(request_mapping, llm_request)
                        response_mapping = merge_mappings(response_mapping, llm_response_map)
                        logger.info(f"LLM enhanced mappings for {api_node.node_key}")
                    except Exception as parse_error:
                        logger.warning(f"Failed to parse TOON response: {parse_error}")
            except Exception as e:
                logger.debug(f"LLM enhancement skipped: {e}")

        # Create binding (endpoint_id may be None if not matched yet)
        binding = EndpointBinding(
            id=None,
            task_id=None,
            flow_node_key=api_node.node_key,
            endpoint_id=matched_endpoint.id if matched_endpoint else None,
            request_mapping=request_mapping,
            response_mapping=response_mapping,
        )
        # Store reference to matched endpoint for code generation
        # (endpoint_id may be None until persistence)
        if matched_endpoint:
            binding._matched_endpoint = matched_endpoint
        state.endpoint_bindings.append(binding)

    state.completed_steps.append("plan_integration_flow")
    return state
