from typing import Dict, Any
from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import Policy, PolicyType


def _extract_security_schemes(state: WorkflowState) -> Dict[str, Any]:
    """
    Extract securitySchemes from parsed OpenAPI specs in state.
    
    Returns dict of scheme_name -> scheme_definition.
    """
    specs = state.plan.get("openapi_specs", [])
    if not specs:
        return {}

    # Use first spec's security schemes
    spec = specs[0]
    components = spec.get("components", {})
    return components.get("securitySchemes", {})


def _infer_auth_policy_config(security_schemes: Dict[str, Any]) -> Dict[str, Any]:
    """
    Infer auth policy configuration from OpenAPI securitySchemes.
    
    Supports:
    - Bearer token (http scheme with bearer)
    - API Key (header or query)
    - OAuth2 (various flows)
    - Basic auth
    - No auth (empty security)
    
    Returns config dict for Policy.
    """
    if not security_schemes:
        return {"type": "none", "description": "No authentication required"}

    # Iterate through schemes and pick the first supported one
    for scheme_name, scheme in security_schemes.items():
        scheme_type = scheme.get("type", "")

        # Bearer token auth (most common for APIs)
        if scheme_type == "http":
            http_scheme = scheme.get("scheme", "").lower()
            if http_scheme == "bearer":
                return {
                    "type": "bearer",
                    "header": "Authorization",
                    "prefix": "Bearer",
                    "scheme_name": scheme_name,
                }
            elif http_scheme == "basic":
                return {
                    "type": "basic",
                    "header": "Authorization",
                    "scheme_name": scheme_name,
                }

        # API Key auth (in header or query)
        elif scheme_type == "apiKey":
            location = scheme.get("in", "header")  # "header", "query", or "cookie"
            key_name = scheme.get("name", "X-API-Key")
            return {
                "type": "api_key",
                "location": location,
                "key_name": key_name,
                "scheme_name": scheme_name,
            }

        # OAuth2 (various flows)
        elif scheme_type == "oauth2":
            flows = scheme.get("flows", {})
            # Prefer client_credentials for server-to-server, then authorizationCode
            if "clientCredentials" in flows:
                flow = flows["clientCredentials"]
                return {
                    "type": "oauth2",
                    "flow": "client_credentials",
                    "token_url": flow.get("tokenUrl", ""),
                    "scopes": list(flow.get("scopes", {}).keys()),
                    "scheme_name": scheme_name,
                }
            elif "authorizationCode" in flows:
                flow = flows["authorizationCode"]
                return {
                    "type": "oauth2",
                    "flow": "authorization_code",
                    "authorization_url": flow.get("authorizationUrl", ""),
                    "token_url": flow.get("tokenUrl", ""),
                    "scopes": list(flow.get("scopes", {}).keys()),
                    "scheme_name": scheme_name,
                }
            elif "implicit" in flows:
                flow = flows["implicit"]
                return {
                    "type": "oauth2",
                    "flow": "implicit",
                    "authorization_url": flow.get("authorizationUrl", ""),
                    "scopes": list(flow.get("scopes", {}).keys()),
                    "scheme_name": scheme_name,
                }

        # OpenID Connect
        elif scheme_type == "openIdConnect":
            return {
                "type": "openid_connect",
                "openid_connect_url": scheme.get("openIdConnectUrl", ""),
                "scheme_name": scheme_name,
            }

    # Default fallback to bearer if we couldn't parse
    return {
        "type": "bearer",
        "header": "Authorization",
        "prefix": "Bearer",
        "inferred": True,  # Mark as fallback
    }


def _infer_rate_limit_config(state: WorkflowState) -> Dict[str, Any]:
    """
    Infer rate limit config from spec extensions or use defaults.
    
    Looks for x-rate-limit extensions in the spec.
    """
    specs = state.plan.get("openapi_specs", [])
    if specs:
        spec = specs[0]
        # Check for x-rate-limit extension at spec level
        rate_limit = spec.get("x-rate-limit", {})
        if rate_limit:
            return {
                "requests_per_second": rate_limit.get("requests_per_second", 10),
                "burst_size": rate_limit.get("burst_size", 20),
                "from_spec": True,
            }

    # Default rate limit
    return {
        "requests_per_second": 10,
        "burst_size": 20,
    }


def attach_policies_and_patterns(state: WorkflowState) -> WorkflowState:
    """
    Attach policies based on spec content (not hardcoded).
    
    Reads: endpoint_bindings, workflow_nodes, plan.openapi_specs
    Writes: policies
    
    M5 Enhancement: Infers auth type from securitySchemes instead of 
    always assuming bearer token.
    """
    if not state.endpoint_bindings:
        state.completed_steps.append("attach_policies_and_patterns")
        return state

    # Extract security schemes from spec
    security_schemes = _extract_security_schemes(state)
    auth_config = _infer_auth_policy_config(security_schemes)
    rate_limit_config = _infer_rate_limit_config(state)

    for binding in state.endpoint_bindings:
        # AUTH policy - inferred from securitySchemes
        auth_policy = Policy(
            id=None,
            task_id=None,
            policy_type=PolicyType.AUTH,
            scope="flow_node",
            scope_ref=binding.flow_node_key,
            config=auth_config,
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
                "redact_fields": ["Authorization", "api_key", "x-api-key"],
            },
        )
        state.policies.append(logging_policy)

        # IDEMPOTENCY policy - add for all bindings
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

        # RATE_LIMIT policy - inferred from spec or defaults
        rate_limit_policy = Policy(
            id=None,
            task_id=None,
            policy_type=PolicyType.RATE_LIMIT,
            scope="flow_node",
            scope_ref=binding.flow_node_key,
            config=rate_limit_config,
        )
        state.policies.append(rate_limit_policy)

    state.completed_steps.append("attach_policies_and_patterns")
    return state
