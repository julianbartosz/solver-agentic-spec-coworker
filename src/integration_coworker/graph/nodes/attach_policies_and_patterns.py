from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import Policy, PolicyType

def attach_policies_and_patterns(state: WorkflowState) -> WorkflowState:
    """
    Reads: endpoint_bindings, workflow_nodes
    Writes: policies
    """
    if not state.endpoint_bindings:
        state.completed_steps.append("attach_policies_and_patterns")
        return state
    
    # For Phase 2: use simple heuristics to attach policies to flow nodes
    
    for binding in state.endpoint_bindings:
        # AUTH policy - always add for protected APIs
        auth_policy = Policy(
            id=None,
            task_id=None,
            policy_type=PolicyType.AUTH,
            scope="flow_node",
            scope_ref=binding.flow_node_key,
            config={
                "type": "bearer",
                "header": "Authorization",
                "prefix": "Bearer",
            },
        )
        state.policies.append(auth_policy)
        
        # RETRY policy - add for API calls
        retry_policy = Policy(
            id=None,
            task_id=None,
            policy_type=PolicyType.RETRY,
            scope="flow_node",
            scope_ref=binding.flow_node_key,
            config={
                "max_attempts": 3,
                "backoff_type": "exponential",
                "initial_delay_ms": 100,
                "max_delay_ms": 5000,
                "retryable_status_codes": [429, 500, 502, 503, 504],
            },
        )
        state.policies.append(retry_policy)
        
        # LOGGING policy - always add
        logging_policy = Policy(
            id=None,
            task_id=None,
            policy_type=PolicyType.LOGGING,
            scope="flow_node",
            scope_ref=binding.flow_node_key,
            config={
                "log_request": True,
                "log_response": True,
                "log_headers": True,
                "redact_fields": ["Authorization", "api_key"],
            },
        )
        state.policies.append(logging_policy)
        
        # IDEMPOTENCY policy - add for all bindings (Phase 2 simplification)
        idempotency_policy = Policy(
            id=None,
            task_id=None,
            policy_type=PolicyType.IDEMPOTENCY,
            scope="flow_node",
            scope_ref=binding.flow_node_key,
            config={
                "header_name": "Idempotency-Key",
                "key_generator": "uuid4",
            },
        )
        state.policies.append(idempotency_policy)
        
        # RATE_LIMIT policy - simple default
        rate_limit_policy = Policy(
            id=None,
            task_id=None,
            policy_type=PolicyType.RATE_LIMIT,
            scope="flow_node",
            scope_ref=binding.flow_node_key,
            config={
                "requests_per_second": 10,
                "burst_size": 20,
            },
        )
        state.policies.append(rate_limit_policy)
    
    state.completed_steps.append("attach_policies_and_patterns")
    return state
