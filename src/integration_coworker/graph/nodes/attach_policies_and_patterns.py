"""
attach_policies_and_patterns node - Attach operational policies to workflow nodes.

V3.0: Converted to async for scalability (ASYNC_MIGRATION_PLAN.md)
"""
import logging
from typing import Dict, Any, List, Optional
from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import Policy, PolicyType

logger = logging.getLogger(__name__)


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


# =============================================================================
# V2.2 (Fix #4): Hybrid Policy Inference Functions
# =============================================================================

def _infer_policies_from_spec(state: WorkflowState) -> Dict[str, Dict[str, Any]]:
    """
    Extract policies from OpenAPI spec structured fields.
    
    V2.2 (Dynamic Capability Fix #4): Enhanced spec extraction that finds
    policy-relevant information in:
    - securitySchemes (auth)
    - x-rate-limit extensions (rate limiting)
    - x-retry extensions (retry)
    - info.x-* extensions (various)
    - Operation-level security requirements
    
    Returns:
        Dict mapping policy type to config:
        {
            "auth": {...},
            "rate_limit": {...},
            "retry": {...},
            "idempotency": {...},
        }
    """
    policies = {}
    
    specs = state.plan.get("openapi_specs", [])
    if not specs:
        return policies
    
    spec = specs[0]
    
    # 1. Auth from securitySchemes
    security_schemes = _extract_security_schemes(state)
    if security_schemes:
        policies["auth"] = _infer_auth_policy_config(security_schemes)
    
    # 2. Rate limit from extensions
    rate_limit = spec.get("x-rate-limit", {})
    if rate_limit:
        policies["rate_limit"] = {
            "requests_per_second": rate_limit.get("requests_per_second", 10),
            "burst_size": rate_limit.get("burst_size", 20),
            "from_spec": True,
        }
    
    # 3. Retry from extensions
    retry = spec.get("x-retry", {})
    if retry:
        policies["retry"] = {
            "max_attempts": retry.get("max_attempts", 3),
            "backoff_type": retry.get("backoff_type", "exponential"),
            "initial_delay_ms": retry.get("initial_delay_ms", 100),
            "from_spec": True,
        }
    
    # 4. Idempotency from extensions
    idempotency = spec.get("x-idempotency", {})
    if idempotency:
        policies["idempotency"] = {
            "header_name": idempotency.get("header_name", "Idempotency-Key"),
            "key_generator": idempotency.get("key_generator", "uuid4"),
            "from_spec": True,
        }
    
    # 5. Look for patterns in info and description
    info = spec.get("info", {})
    description = info.get("description", "")
    
    # Simple pattern matching for common rate limit mentions
    if description:
        desc_lower = description.lower()
        
        # Rate limit patterns
        if "rate limit" in desc_lower or "ratelimit" in desc_lower:
            if "rate_limit" not in policies:
                policies["rate_limit"] = {
                    "requests_per_second": 10,
                    "burst_size": 20,
                    "inferred_from_description": True,
                }
        
        # Retry patterns
        if "retry" in desc_lower or "exponential backoff" in desc_lower:
            if "retry" not in policies:
                policies["retry"] = {
                    "max_attempts": 3,
                    "backoff_type": "exponential",
                    "inferred_from_description": True,
                }
        
        # Idempotency patterns
        if "idempotent" in desc_lower or "idempotency" in desc_lower:
            if "idempotency" not in policies:
                policies["idempotency"] = {
                    "header_name": "Idempotency-Key",
                    "key_generator": "uuid4",
                    "inferred_from_description": True,
                }
    
    logger.debug(f"Spec-inferred policies: {list(policies.keys())}")
    return policies


async def _augment_with_llm_analysis(
    state: WorkflowState,
    spec_policies: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """
    Augment spec-extracted policies with LLM prose analysis.
    
    V2.2 (Dynamic Capability Fix #4): Uses LLM to analyze prose in API
    documentation that isn't captured by structured fields. This catches:
    - Rate limits mentioned in descriptions
    - Retry recommendations in error responses
    - Auth requirements in security notes
    - Idempotency guidance in operation descriptions
    
    V3.0: Converted to async for scalability (ASYNC_MIGRATION_PLAN.md)
    
    Args:
        state: WorkflowState with spec data
        spec_policies: Policies already extracted from structured spec fields
        
    Returns:
        Updated policy dict with LLM-augmented info merged in
    """
    # Start with spec-extracted policies
    policies = dict(spec_policies)
    
    # Collect prose from spec for LLM analysis
    prose_sections = []
    
    specs = state.plan.get("openapi_specs", [])
    if specs:
        spec = specs[0]
        
        # Collect description prose
        info = spec.get("info", {})
        if info.get("description"):
            prose_sections.append(f"API Description:\n{info['description'][:2000]}")
        
        # Collect endpoint descriptions (first few)
        paths = spec.get("paths", {})
        for path, path_item in list(paths.items())[:5]:
            for method, operation in path_item.items():
                if isinstance(operation, dict):
                    desc = operation.get("description", "")
                    summary = operation.get("summary", "")
                    if desc or summary:
                        prose_sections.append(
                            f"Endpoint {method.upper()} {path}:\n{summary}\n{desc[:500]}"
                        )
    
    if not prose_sections:
        logger.debug("No prose sections to analyze, using spec-only policies")
        return policies
    
    # Build prompt for LLM analysis
    prose_text = "\n\n".join(prose_sections[:10])  # Limit to avoid token overflow
    
    prompt = f"""Analyze this API documentation and extract policy recommendations.

## API DOCUMENTATION
{prose_text}

## TASK
Extract any mentions of:
1. Rate limits (requests per second, quotas)
2. Retry behavior (max attempts, backoff strategy)
3. Authentication requirements
4. Idempotency recommendations

## OUTPUT FORMAT
Return a JSON object with keys: rate_limit, retry, auth, idempotency
Only include keys where you found relevant information.
For each, include the specific values mentioned or recommended.

Example:
{{"rate_limit": {{"requests_per_second": 100, "notes": "Per API key"}},
 "retry": {{"max_attempts": 3, "on_status": [429, 500]}}}}

Return ONLY valid JSON, no markdown fences:
"""
    
    try:
        from integration_coworker.llm import get_async_llm_client_for_node
        import json
        
        client = get_async_llm_client_for_node("attach_policies_and_patterns")
        response = await client.complete_async(prompt)
        
        if response:
            # Clean any markdown fences
            clean_response = response.strip()
            if clean_response.startswith("```"):
                clean_response = clean_response.split("```")[1]
                if clean_response.startswith("json"):
                    clean_response = clean_response[4:]
            clean_response = clean_response.rstrip("`")
            
            try:
                llm_policies = json.loads(clean_response)
                
                # Merge LLM policies with spec policies (spec takes precedence)
                for policy_type, config in llm_policies.items():
                    if policy_type not in policies and isinstance(config, dict):
                        config["llm_inferred"] = True
                        policies[policy_type] = config
                        logger.info(f"LLM inferred {policy_type} policy from prose")
                    elif policy_type in policies:
                        # Augment existing policy with LLM insights
                        if isinstance(config, dict) and "notes" in config:
                            policies[policy_type]["llm_notes"] = config["notes"]
                
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse LLM policy response: {e}")
                
    except Exception as e:
        logger.warning(f"LLM policy analysis failed: {e}")
    
    return policies


def _create_policy_from_config(
    policy_type: PolicyType,
    config: Dict[str, Any],
    scope: str,
    scope_ref: str,
) -> Policy:
    """Create a Policy object from config dict."""
    return Policy(
        id=None,
        task_id=None,
        policy_type=policy_type,
        scope=scope,
        scope_ref=scope_ref,
        config=config,
    )


async def attach_policies_and_patterns(state: WorkflowState) -> WorkflowState:
    """
    Attach policies based on spec content using hybrid inference.
    
    Reads: endpoint_bindings, workflow_nodes, plan.openapi_specs
    Writes: policies
    
    V2.2 (Fix #4): Uses hybrid policy inference:
    1. Extract policies from structured spec fields (x-rate-limit, securitySchemes)
    2. Augment with LLM analysis of prose descriptions
    3. Apply sensible defaults for missing policies
    
    V3.0: Converted to async for scalability (ASYNC_MIGRATION_PLAN.md)
    
    This replaces the previous static inference approach.
    """
    if not state.endpoint_bindings:
        state.completed_steps.append("attach_policies_and_patterns")
        return state

    # -------------------------------------------------------------------------
    # Step 1: Infer policies from spec (structured fields + patterns)
    # -------------------------------------------------------------------------
    spec_policies = _infer_policies_from_spec(state)
    
    # -------------------------------------------------------------------------
    # Step 2: Augment with LLM prose analysis (optional, may fail gracefully)
    # -------------------------------------------------------------------------
    try:
        inferred_policies = await _augment_with_llm_analysis(state, spec_policies)
        logger.info(f"Hybrid policy inference found: {list(inferred_policies.keys())}")
    except Exception as e:
        logger.warning(f"LLM augmentation failed, using spec-only policies: {e}")
        inferred_policies = spec_policies
    
    # -------------------------------------------------------------------------
    # Step 3: Apply policies to each endpoint binding
    # -------------------------------------------------------------------------
    for binding in state.endpoint_bindings:
        scope_ref = binding.flow_node_key
        
        # AUTH policy - from inference or fallback to bearer
        auth_config = inferred_policies.get("auth") or _infer_auth_policy_config(
            _extract_security_schemes(state)
        )
        auth_policy = _create_policy_from_config(
            PolicyType.AUTH, auth_config, "flow_node", scope_ref
        )
        state.policies.append(auth_policy)

        # RETRY policy - from inference or defaults
        retry_config = inferred_policies.get("retry") or {
            "max_attempts": 3,
            "backoff_type": "exponential",
            "initial_delay_ms": 100,
            "max_delay_ms": 5000,
            "retryable_status_codes": [429, 500, 502, 503, 504],
        }
        retry_policy = _create_policy_from_config(
            PolicyType.RETRY, retry_config, "flow_node", scope_ref
        )
        state.policies.append(retry_policy)

        # LOGGING policy - always add (standard defaults)
        logging_config = {
            "log_request": True,
            "log_response": True,
            "log_headers": True,
            "redact_fields": ["Authorization", "api_key", "x-api-key"],
        }
        logging_policy = _create_policy_from_config(
            PolicyType.LOGGING, logging_config, "flow_node", scope_ref
        )
        state.policies.append(logging_policy)

        # IDEMPOTENCY policy - from inference or defaults
        idempotency_config = inferred_policies.get("idempotency") or {
            "header_name": "Idempotency-Key",
            "key_generator": "uuid4",
        }
        idempotency_policy = _create_policy_from_config(
            PolicyType.IDEMPOTENCY, idempotency_config, "flow_node", scope_ref
        )
        state.policies.append(idempotency_policy)

        # RATE_LIMIT policy - from inference or defaults
        rate_limit_config = inferred_policies.get("rate_limit") or {
            "requests_per_second": 10,
            "burst_size": 20,
        }
        rate_limit_policy = _create_policy_from_config(
            PolicyType.RATE_LIMIT, rate_limit_config, "flow_node", scope_ref
        )
        state.policies.append(rate_limit_policy)

    state.completed_steps.append("attach_policies_and_patterns")
    return state
