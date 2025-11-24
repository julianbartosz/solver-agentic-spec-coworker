from integration_coworker.graph.state import WorkflowState


def validate_integration_design(state: WorkflowState) -> WorkflowState:
    """
    Reads: integration_task, workflow_nodes, workflow_edges, endpoint_bindings, code_artifacts, errors
    Writes: validation results (adds to errors if issues found)
    
    Contract per Appendix C.3.14:
    - Enforces flow structure semantics (start/end, connectivity, positions)
    - Validates endpoint binding consistency
    - Raises on critical failures
    """
    validation_errors = []
    
    # Check that we have core components
    if not state.integration_task:
        validation_errors.append("Validation failed: No integration_task defined")
    
    if not state.workflow_nodes:
        validation_errors.append("Validation failed: No workflow_nodes defined")
    
    if not state.code_artifacts:
        # Warning only for M3
        validation_errors.append("Validation warning: No code_artifacts generated")
    
    # Check that endpoints were found
    if not state.endpoints:
        validation_errors.append("Validation warning: No endpoints extracted from spec")
    
    # 1. Validate flow structure per Appendix H.5
    if state.workflow_nodes:
        start_nodes = [n for n in state.workflow_nodes if n.node_type == "start"]
        end_nodes = [n for n in state.workflow_nodes if n.node_type == "end"]
        
        if len(start_nodes) != 1:
            validation_errors.append(f"Flow must have exactly one start node, found {len(start_nodes)}")
        
        if len(end_nodes) < 1:
            validation_errors.append("Flow must have at least one end node, found 0")
        
        # Check connectivity
        edge_map = {}
        reverse_edge_map = {}
        
        for edge in state.workflow_edges:
            edge_map.setdefault(edge.from_node_key, []).append(edge.to_node_key)
            reverse_edge_map.setdefault(edge.to_node_key, []).append(edge.from_node_key)
        
        # All non-start nodes must have incoming edges
        for node in state.workflow_nodes:
            if node.node_type != "start" and node.node_key not in reverse_edge_map:
                validation_errors.append(f"Node '{node.node_key}' has no incoming edges")
        
        # All non-end nodes must have outgoing edges
        for node in state.workflow_nodes:
            if node.node_type != "end" and node.node_key not in edge_map:
                validation_errors.append(f"Node '{node.node_key}' has no outgoing edges")
        
        # Check position strictly increases
        node_positions = {n.node_key: n.position for n in state.workflow_nodes}
        for edge in state.workflow_edges:
            from_pos = node_positions.get(edge.from_node_key, -1)
            to_pos = node_positions.get(edge.to_node_key, -1)
            if from_pos >= to_pos:
                validation_errors.append(
                    f"Position must increase along edges: {edge.from_node_key}(pos={from_pos}) -> {edge.to_node_key}(pos={to_pos})"
                )
    
    # 2. Validate endpoint bindings
    api_call_nodes = [n for n in state.workflow_nodes if n.node_type == "api_call"]
    binding_keys = {b.flow_node_key for b in state.endpoint_bindings}
    
    for api_node in api_call_nodes:
        if api_node.node_key not in binding_keys:
            validation_errors.append(
                f"API call node '{api_node.node_key}' has no matching EndpointBinding"
            )
        else:
            # Check for endpoint_id
            matching_bindings = [b for b in state.endpoint_bindings if b.flow_node_key == api_node.node_key]
            for binding in matching_bindings:
                if binding.endpoint_id is None:
                    # Warning only for M3
                    validation_errors.append(
                        f"Warning: EndpointBinding for node '{api_node.node_key}' has endpoint_id=None"
                    )
    
    # Add all validation errors to state
    state.errors.extend(validation_errors)
    
    # For M3: don't raise on warnings, only on critical structural failures
    critical_failures = [e for e in validation_errors if "Warning" not in e and "warning" not in e]
    if critical_failures:
        error_msg = f"Validation failed with {len(critical_failures)} critical error(s)"
        raise ValueError(error_msg)
    
    state.completed_steps.append("validate_integration_design")
    return state
