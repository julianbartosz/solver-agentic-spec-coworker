"""
LLM prompt builders for code generation.

Builds rich, context-aware prompts that help the LLM generate
high-quality, spec-driven, repo-fitting code.

v2: Implements FT-SEC-002 - Input sanitization for task descriptions
"""
from typing import Literal, Optional, Dict, Any

from integration_coworker.domain.models import Endpoint, IntegrationTask
from integration_coworker.repo.models import RepoProfile
from integration_coworker.graph.state import WorkflowState
from integration_coworker.llm.sanitizer import sanitize_task_description


def build_codegen_prompt(
    state: WorkflowState,
    endpoint: Optional[Endpoint],
    task: Optional[IntegrationTask],
    repo_profile: Optional[RepoProfile],
    artifact_kind: Literal["client", "flow", "test"],
    skeleton_code: str,
    client_class: Optional[str] = None,
    method_name: Optional[str] = None,
    flow_function: Optional[str] = None,
    import_statements: Optional[str] = None,
) -> str:
    """
    Build a comprehensive prompt for LLM code generation.
    
    This prompt includes:
    - Task and provider context
    - Endpoint details (method, path, schemas)
    - Target repo style from RepoProfile
    - Skeleton code to fill in
    - Non-negotiable constraints
    
    Args:
        state: WorkflowState with full context
        endpoint: Primary API endpoint
        task: Integration task with description
        repo_profile: Target repo profile
        artifact_kind: "client", "flow", or "test"
        skeleton_code: Template code with signatures to fill in
        client_class: Client class name (for client artifact)
        method_name: Method name (for client artifact)
        flow_function: Flow function name (for flow artifact)
        import_statements: Pre-computed import statements
    
    Returns:
        A detailed prompt string for the LLM
    """
    provider_code = state.provider_code or "unknown"
    
    # v2: Sanitize task description to prevent prompt injection (SEC-002)
    raw_task_desc = task.description if task else state.task_description or ""
    task_desc = sanitize_task_description(raw_task_desc) or "API integration"

    # Build endpoint context
    endpoint_context = _build_endpoint_context(endpoint)

    # Build repo style context
    repo_context = _build_repo_context(repo_profile)

    # Build artifact-specific instructions
    artifact_instructions = _build_artifact_instructions(
        artifact_kind=artifact_kind,
        client_class=client_class,
        method_name=method_name,
        flow_function=flow_function,
    )

    # Build schema context if available
    schema_context = _build_schema_context(state, endpoint)

    prompt = f"""You are a senior Python developer generating production-quality API integration code.

## TASK
Provider: {provider_code}
Task: {task_desc}
Artifact Type: {artifact_kind}

## ENDPOINT DETAILS
{endpoint_context}

## REQUEST/RESPONSE SCHEMAS
{schema_context}

## TARGET REPOSITORY STYLE
{repo_context}

## ARTIFACT REQUIREMENTS
{artifact_instructions}

## SKELETON CODE
Fill in the function/class bodies in this skeleton. Keep the imports, class name, method name, and signatures EXACTLY as provided.

```python
{skeleton_code}
```

## CONSTRAINTS (MUST FOLLOW)
1. Return ONLY valid Python code, no markdown fences or explanations
2. Keep all imports, class names, method names, and function signatures exactly as provided
3. Add comprehensive docstrings and type hints
4. Include proper error handling (raise appropriate exceptions)
5. Do NOT hardcode any URLs, API keys, or test data
6. Follow Python best practices and PEP 8 style

## OUTPUT
Return the complete, refined Python code with filled-in function bodies:
"""

    return prompt


def _build_endpoint_context(endpoint: Optional[Endpoint]) -> str:
    """Build context string for the endpoint."""
    if not endpoint:
        return "No specific endpoint provided."

    lines = [
        f"HTTP Method: {endpoint.method.upper()}",
        f"Path: {endpoint.path}",
    ]

    if endpoint.operation_id:
        lines.append(f"Operation ID: {endpoint.operation_id}")

    if endpoint.summary:
        lines.append(f"Summary: {endpoint.summary}")

    if endpoint.description:
        lines.append(f"Description: {endpoint.description}")

    if endpoint.auth_required:
        lines.append("Authentication: Required (Bearer token)")

    return "\n".join(lines)


def _build_repo_context(repo_profile: Optional[RepoProfile]) -> str:
    """Build context string for the repo profile."""
    if not repo_profile:
        return "Standard Python package structure."

    lines = [
        f"Framework: {repo_profile.framework or 'generic Python'}",
        f"Language: {repo_profile.language or 'python'}",
    ]

    if repo_profile.archetype:
        lines.append(f"Archetype: {repo_profile.archetype}")

    if repo_profile.conventions:
        lines.append(f"Conventions: {repo_profile.conventions}")

    return "\n".join(lines)


def _build_artifact_instructions(
    artifact_kind: str,
    client_class: Optional[str],
    method_name: Optional[str],
    flow_function: Optional[str],
) -> str:
    """Build artifact-specific instructions."""
    if artifact_kind == "client":
        return f"""Generate an API client class.
- Class name must be: {client_class}
- Main method must be: {method_name}
- The method should accept a payload dict and optional idempotency_key
- Handle HTTP errors by raising IntegrationError with status code and message
- Support configurable base_url via constructor"""

    elif artifact_kind == "flow":
        return f"""Generate a workflow/flow function.
- Function name must be: {flow_function}
- Accept api_key, payload, and **kwargs
- Validate input parameters before calling the API
- Transform and return the API response
- Raise ValueError for validation errors
- Raise IntegrationError (via the client) for API errors"""

    elif artifact_kind == "test":
        return """Generate pytest test cases.
- Use pytest and unittest.mock
- Mock the API client to avoid real network calls
- Test both success and error scenarios
- Use meaningful test data that reflects the API
- Test input validation"""

    return "Generate clean, well-documented Python code."


def _build_schema_context(state: WorkflowState, endpoint: Optional[Endpoint]) -> str:
    """Build context string for request/response schemas."""
    if not state.openapi_spec:
        return "Schema details not available."

    lines = []

    # Try to find request body schema
    if endpoint:
        paths = state.openapi_spec.get("paths", {})
        path_item = paths.get(endpoint.path, {})
        operation = path_item.get(endpoint.method.lower(), {})

        # Request body
        request_body = operation.get("requestBody", {})
        if request_body:
            content = request_body.get("content", {})
            json_content = content.get("application/json", {})
            schema = json_content.get("schema", {})
            if schema:
                lines.append("Request Body Schema:")
                lines.append(_format_schema(schema, state.openapi_spec))

        # Response schema
        responses = operation.get("responses", {})
        success_response = responses.get("200", responses.get("201", {}))
        if success_response:
            content = success_response.get("content", {})
            json_content = content.get("application/json", {})
            schema = json_content.get("schema", {})
            if schema:
                lines.append("\nResponse Schema:")
                lines.append(_format_schema(schema, state.openapi_spec))

    return "\n".join(lines) if lines else "Schema details not available in spec."


def _format_schema(schema: Dict[str, Any], spec: Dict[str, Any], depth: int = 0) -> str:
    """Format a JSON schema for the prompt."""
    indent = "  " * depth
    lines = []

    # Handle $ref
    if "$ref" in schema:
        ref = schema["$ref"]
        ref_name = ref.split("/")[-1]

        # Resolve the reference
        components = spec.get("components", {})
        schemas = components.get("schemas", {})
        resolved = schemas.get(ref_name, {})

        if resolved:
            lines.append(f"{indent}{ref_name}:")
            lines.append(_format_schema(resolved, spec, depth + 1))
        else:
            lines.append(f"{indent}$ref: {ref_name}")
        return "\n".join(lines)

    # Handle object type
    if schema.get("type") == "object":
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))

        for name, prop_schema in properties.items():
            req_marker = "*" if name in required else ""
            prop_type = prop_schema.get("type", "any")
            desc = prop_schema.get("description", "")
            example = prop_schema.get("example", "")

            line = f"{indent}- {name}{req_marker}: {prop_type}"
            if desc:
                line += f" ({desc})"
            if example:
                line += f" [example: {example}]"
            lines.append(line)

    elif schema.get("type") == "array":
        items = schema.get("items", {})
        lines.append(f"{indent}Array of:")
        lines.append(_format_schema(items, spec, depth + 1))

    else:
        # Primitive type
        prop_type = schema.get("type", "any")
        lines.append(f"{indent}Type: {prop_type}")

    return "\n".join(lines)
