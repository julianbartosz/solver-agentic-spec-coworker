"""
plan_integration_flow node - Validate and finalize workflow structure, create endpoint bindings.

Uses LLM with TOON format when available to enhance endpoint binding mappings.
Uses schema-based field mapping generation for deterministic mappings.

Per design doc Section 5.5:
- Archetype: plan_integration_flow.archetype.yaml
- Provider: Anthropic (claude-3-sonnet) - complex workflow planning
- Strategy: chain_of_thought with best-of-n sampling

V3.0: Converted to async for scalability (ASYNC_MIGRATION_PLAN.md)
V3 FILE: Extended to handle parse_file nodes for file-based operations.
"""
import logging
from collections import deque
from typing import List, Tuple

from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import IntegrationFlowNode, IntegrationFlowEdge
from integration_coworker.domain.models import EndpointBinding
from integration_coworker.domain.ir import ProtocolType
from integration_coworker.llm import get_async_llm_client_for_node
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


async def plan_integration_flow(state: WorkflowState) -> WorkflowState:
    """
    Reads: integration_task, workflow_nodes, workflow_edges, endpoints, plan
    Writes: endpoint_bindings
    
    Contract per Appendix C.3.8:
    - Validates flow structure (start/end nodes, connectivity, positions)
    - Creates EndpointBinding scaffolds for each api_call node
    - Uses LLM to enhance binding mappings when available
    
    V3.0: Converted to async for scalability (ASYNC_MIGRATION_PLAN.md)
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
    
    # V3 FILE: Identify parse_file nodes for file-based operations
    parse_file_nodes = [n for n in state.workflow_nodes if n.node_type == "parse_file"]
    
    # Get protocol type from task constraints
    protocol_type = ProtocolType.REST.value  # Default
    if state.integration_task and state.integration_task.constraints:
        protocol_type = state.integration_task.constraints.get("extra", {}).get(
            "protocol_type", ProtocolType.REST.value
        )

    # V3 FILE: Handle file-based flows
    if protocol_type == ProtocolType.FILE.value or parse_file_nodes:
        # File-based flow - create file operation bindings
        target_operations = []
        if state.integration_task and state.integration_task.constraints:
            target_operations = state.integration_task.constraints.get("extra", {}).get("target_operations", [])
        
        logger.info(f"[DEBUG] FILE flow detected. parse_file_nodes: {len(parse_file_nodes)}, target_operations: {len(target_operations)}")
        
        for file_node in parse_file_nodes:
            # Match to a FileSpec via target_operations
            matched_file_spec = None
            
            for target_op in target_operations:
                if isinstance(target_op, dict):
                    file_spec_id = target_op.get("file_spec_id")
                    if file_spec_id is not None:
                        # Find matching FileSpec
                        for fs in state.file_specs:
                            if fs.id == file_spec_id:
                                matched_file_spec = fs
                                break
                        if matched_file_spec:
                            break
            
            # If no match from target_operations, use first file spec
            if not matched_file_spec and state.file_specs:
                matched_file_spec = state.file_specs[0]
                logger.warning(f"No target_operation match for {file_node.node_key}, using first file_spec: {matched_file_spec.name}")
            
            # Create binding for file operation
            # Note: We reuse EndpointBinding structure with file-specific metadata
            binding = EndpointBinding(
                id=None,
                task_id=None,
                flow_node_key=file_node.node_key,
                endpoint_id=None,  # No endpoint for file ops
                request_mapping={
                    "file_spec_id": matched_file_spec.id if matched_file_spec else None,
                    "file_spec_name": matched_file_spec.name if matched_file_spec else None,
                    "file_type": matched_file_spec.file_type if matched_file_spec else None,
                    "protocol_type": ProtocolType.FILE.value,
                    "op": "parse",
                },
                response_mapping={
                    "output_type": "list_of_records",
                },
            )
            if matched_file_spec:
                binding._matched_file_spec = matched_file_spec
            state.endpoint_bindings.append(binding)
            logger.info(f"Created file binding for {file_node.node_key} -> {matched_file_spec.name if matched_file_spec else 'None'}")
        
        # If we processed file nodes, we're done (even if no api_call nodes)
        if parse_file_nodes:
            state.completed_steps.append("plan_integration_flow")
            return state

    if not api_call_nodes:
        # No API calls in this flow - acceptable for some workflows
        state.completed_steps.append("plan_integration_flow")
        return state

    # Get target operations from integration_task constraints
    target_operations = []
    if state.integration_task and state.integration_task.constraints:
        target_operations = state.integration_task.constraints.get("extra", {}).get("target_operations", [])

    # Debug logging: Understand why LLM call may be skipped
    logger.info(f"[DEBUG] target_operations count: {len(target_operations)}")
    logger.info(f"[DEBUG] target_operations content: {target_operations[:2] if target_operations else 'empty'}")
    logger.info(f"[DEBUG] degraded_mode: {getattr(state, 'degraded_mode', False)}")
    logger.info(f"[DEBUG] provider_code: {state.provider_code}")
    if state.integration_task:
        task_source = getattr(state.integration_task, '_task_source', 'unknown')
        logger.info(f"[DEBUG] task_source: {task_source}")
        logger.info(f"[DEBUG] constraints.extra: {state.integration_task.constraints.get('extra', {})}")

    # Create bindings for each api_call node
    for api_node in api_call_nodes:
        # Match to an endpoint
        matched_endpoint = None

        if target_operations:
            # Try to match first target operation to this node
            target_op = target_operations[0] if target_operations else None
            # Bug #14 Fix: Ensure target_op is a dict, not a string
            # LLM may return malformed target_operations as strings
            if target_op and isinstance(target_op, dict):
                operation_id = target_op.get("operation_id")
                method = target_op.get("method")
                path = target_op.get("path")

                for endpoint in state.endpoints:
                    if endpoint.operation_id == operation_id or \
                       (endpoint.method == method and endpoint.path == path):
                        matched_endpoint = endpoint
                        break
            elif target_op and isinstance(target_op, str):
                # Handle case where LLM returned a string (e.g., operation_id only)
                logger.warning(f"target_operation is a string, not dict: {target_op}")
                for endpoint in state.endpoints:
                    if endpoint.operation_id == target_op or target_op in endpoint.path:
                        matched_endpoint = endpoint
                        break

        # Generate schema-based mappings first
        request_mapping = {}
        response_mapping = {}

        # Debug logging: Endpoint matching result
        logger.info(f"[DEBUG] api_node: {api_node.node_key}, matched_endpoint: {matched_endpoint.operation_id if matched_endpoint else 'None'}")
        if not matched_endpoint and target_operations:
            logger.info(f"[DEBUG] Endpoint matching FAILED. Available endpoints: {[e.operation_id for e in state.endpoints[:5]]}")

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

            # Optionally enhance with LLM (sync factory, async methods)
            try:
                client = get_async_llm_client_for_node(NODE_NAME)

                prompt = _build_binding_prompt(state, api_node, matched_endpoint)
                llm_response_text = await client.complete_async(prompt)

                # V2: Removed string-based mock detection (LLM-006)
                # If LLM returns invalid TOON, the parse will fail and we keep schema mappings
                if llm_response_text:
                    try:
                        llm_response = from_toon(llm_response_text)
                        del llm_response_text  # V22: Release raw LLM response after parsing
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
