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

from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import EndpointBinding
from integration_coworker.llm import get_llm_client_for_node
from integration_coworker.llm.toon import from_toon
from integration_coworker.codegen.field_mappings import (
    generate_field_mappings,
    merge_mappings,
)

logger = logging.getLogger(__name__)

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

    # 1. Validate flow structure per Appendix H.5
    start_nodes = [n for n in state.workflow_nodes if n.node_type == "start"]
    end_nodes = [n for n in state.workflow_nodes if n.node_type == "end"]

    if len(start_nodes) != 1:
        error = f"Flow must have exactly one start node, found {len(start_nodes)}"
        state.errors.append(error)
        raise ValueError(error)

    if len(end_nodes) < 1:
        error = "Flow must have at least one end node"
        state.errors.append(error)
        raise ValueError(error)

    # Check connectivity (simplified: assumes linear flow for M3)
    edge_map = {}  # from_key -> [to_keys]
    reverse_edge_map = {}  # to_key -> [from_keys]

    for edge in state.workflow_edges:
        edge_map.setdefault(edge.from_node_key, []).append(edge.to_node_key)
        reverse_edge_map.setdefault(edge.to_node_key, []).append(edge.from_node_key)

    # All non-start nodes must have incoming edges
    for node in state.workflow_nodes:
        if node.node_type != "start" and node.node_key not in reverse_edge_map:
            error = f"Node {node.node_key} has no incoming edges"
            state.errors.append(error)
            raise ValueError(error)

    # All non-end nodes must have outgoing edges
    for node in state.workflow_nodes:
        if node.node_type != "end" and node.node_key not in edge_map:
            error = f"Node {node.node_key} has no outgoing edges"
            state.errors.append(error)
            raise ValueError(error)

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

                if llm_response_text and not llm_response_text.startswith("Mock response"):
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
