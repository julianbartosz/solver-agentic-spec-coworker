"""
LLM prompt builders for code generation.

Builds rich, context-aware prompts that help the LLM generate
high-quality, spec-driven, repo-fitting code.

v2: Implements FT-SEC-002 - Input sanitization for task descriptions
v3 (Bug #70): Dynamic language support - prompts adapt to target language
v4 (V38-006): Enhanced request body examples and required field extraction
"""
import json
import re
from typing import Literal, Optional, Dict, Any, List, Tuple

from integration_coworker.domain.models import Endpoint, IntegrationTask
from integration_coworker.repo.models import RepoProfile
from integration_coworker.graph.state import WorkflowState
from integration_coworker.llm.sanitizer import sanitize_task_description


def _resolve_schema_with_allof(schema: Dict[str, Any], spec: Dict[str, Any], visited: Optional[set] = None) -> Dict[str, Any]:
    """
    V38-006: Recursively resolve $ref and allOf to get complete schema structure.
    
    This is critical for APIs like OpenAI where schemas use allOf for inheritance.
    Without this, we miss required fields defined in parent schemas.
    
    Args:
        schema: Schema dict (may contain $ref or allOf)
        spec: Full OpenAPI spec for reference resolution
        visited: Set of visited refs to prevent infinite recursion
        
    Returns:
        Resolved schema with all properties and required fields merged
    """
    if visited is None:
        visited = set()
    
    # Handle $ref
    if "$ref" in schema:
        ref = schema["$ref"]
        ref_name = ref.split("/")[-1]
        if ref_name in visited:
            return {"type": "object", "properties": {}, "required": []}
        visited.add(ref_name)
        
        resolved = spec.get("components", {}).get("schemas", {}).get(ref_name, {})
        return _resolve_schema_with_allof(resolved, spec, visited)
    
    result = {
        "type": schema.get("type", "object"),
        "properties": {},
        "required": list(schema.get("required", [])),
    }
    
    # Handle allOf - merge all schemas
    if "allOf" in schema:
        for item in schema["allOf"]:
            resolved = _resolve_schema_with_allof(item, spec, visited.copy())
            result["properties"].update(resolved.get("properties", {}))
            result["required"].extend(resolved.get("required", []))
    
    # Handle anyOf/oneOf - take first option's properties for example purposes
    for key in ["anyOf", "oneOf"]:
        if key in schema and schema[key]:
            first_option = _resolve_schema_with_allof(schema[key][0], spec, visited.copy())
            result["properties"].update(first_option.get("properties", {}))
            # Don't merge required from anyOf/oneOf - they're alternatives
    
    # Merge direct properties
    if "properties" in schema:
        result["properties"].update(schema["properties"])
    
    # Dedupe required fields
    result["required"] = list(set(result["required"]))
    
    return result


def _extract_request_example(operation: Dict[str, Any], spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    V38-006: Extract a concrete request body example from the OpenAPI operation.
    
    Looks for examples in multiple places (priority order):
    1. x-oaiMeta.examples.request.body (OpenAI-style examples)
    2. requestBody.content.application/json.example
    3. requestBody.content.application/json.examples (first one)
    4. Generate minimal example from schema required fields
    
    Args:
        operation: OpenAPI operation dict
        spec: Full OpenAPI spec
        
    Returns:
        Example request body dict, or None if not available
    """
    # Strategy 1: OpenAI-style x-oaiMeta examples
    x_oai_meta = operation.get("x-oaiMeta", {})
    if isinstance(x_oai_meta, dict):
        examples = x_oai_meta.get("examples", {})
        if isinstance(examples, dict):
            request_example = examples.get("request", {})
            if isinstance(request_example, dict):
                # Try to get body directly
                body = request_example.get("body")
                if body:
                    return body
                # Try to parse from curl
                curl = request_example.get("curl", "")
                if curl and "-d" in curl:
                    # Extract JSON from curl -d '...' or -d "..."
                    match = re.search(r"-d\s+['\"](\{.+?\})['\"]", curl, re.DOTALL)
                    if match:
                        try:
                            return json.loads(match.group(1))
                        except json.JSONDecodeError:
                            pass
    
    # Strategy 2 & 3: Standard OpenAPI examples
    request_body = operation.get("requestBody", {})
    content = request_body.get("content", {}).get("application/json", {})
    
    # Direct example
    if "example" in content:
        return content["example"]
    
    # Examples collection (take first)
    examples = content.get("examples", {})
    if examples:
        first_example = next(iter(examples.values()), {})
        if isinstance(first_example, dict) and "value" in first_example:
            return first_example["value"]
    
    # Strategy 4: Generate minimal example from schema
    schema = content.get("schema", {})
    if schema:
        return _generate_minimal_example(schema, spec)
    
    return None


def _generate_minimal_example(schema: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    """
    V38-006: Generate a minimal valid request body example from schema.
    
    Uses required fields and example values from the schema.
    """
    resolved = _resolve_schema_with_allof(schema, spec)
    
    example = {}
    required = set(resolved.get("required", []))
    properties = resolved.get("properties", {})
    
    for name in required:
        if name in properties:
            prop = properties[name]
            example[name] = _get_example_value(prop, spec, name)
    
    return example


def _get_example_value(prop: Dict[str, Any], spec: Dict[str, Any], name: str = "") -> Any:
    """Get an example value for a schema property."""
    # Use explicit example if available
    if "example" in prop:
        return prop["example"]
    if "default" in prop:
        return prop["default"]
    
    # Resolve $ref if present
    if "$ref" in prop:
        ref_name = prop["$ref"].split("/")[-1]
        resolved_schema = spec.get("components", {}).get("schemas", {}).get(ref_name, {})
        # Check if the resolved schema has an example
        if "example" in resolved_schema:
            return resolved_schema["example"]
        # Check if it's an enum type (common for model fields)
        if "enum" in resolved_schema:
            return resolved_schema["enum"][0]
        # Check anyOf/oneOf for enum (common pattern for model IDs)
        for key in ["anyOf", "oneOf"]:
            if key in resolved_schema:
                for option in resolved_schema[key]:
                    if "enum" in option:
                        return option["enum"][0]
        # Recursively resolve
        resolved = _resolve_schema_with_allof(prop, spec)
        prop = resolved
    
    prop_type = prop.get("type", "string")
    
    # Handle enums
    if "enum" in prop:
        return prop["enum"][0]
    
    # Handle anyOf/oneOf (pick first concrete option)
    for key in ["anyOf", "oneOf"]:
        if key in prop and prop[key]:
            first_option = prop[key][0]
            if "enum" in first_option:
                return first_option["enum"][0]
            return _get_example_value(first_option, spec, name)
    
    # Generate based on type
    if prop_type == "string":
        format_val = prop.get("format", "")
        if format_val == "date-time":
            return "2024-01-01T00:00:00Z"
        if format_val == "date":
            return "2024-01-01"
        if format_val == "email":
            return "user@example.com"
        if format_val == "uri":
            return "https://example.com"
        # Use field name as hint for generic placeholders
        if "id" in name.lower():
            return "example_id_12345"
        if "name" in name.lower():
            return "example_name"
        # Generic placeholder using field name
        return f"example_{name}" if name else "example_value"
    
    if prop_type == "integer":
        return prop.get("minimum", 1)
    
    if prop_type == "number":
        return prop.get("minimum", 1.0)
    
    if prop_type == "boolean":
        return True
    
    if prop_type == "array":
        items = prop.get("items", {})
        item_example = _get_example_value(items, spec)
        return [item_example]
    
    if prop_type == "object":
        # Generate nested example for required fields only
        nested = {}
        nested_required = set(prop.get("required", []))
        for nested_name in nested_required:
            if nested_name in prop.get("properties", {}):
                nested[nested_name] = _get_example_value(prop["properties"][nested_name], spec, nested_name)
        return nested if nested else {"key": "value"}
    
    return None


def _format_required_structure(schema: Dict[str, Any], spec: Dict[str, Any]) -> str:
    """
    V38-006: Format the required request body structure for the prompt.
    
    Shows which fields are required and their expected types/structure.
    """
    resolved = _resolve_schema_with_allof(schema, spec)
    
    required = set(resolved.get("required", []))
    properties = resolved.get("properties", {})
    
    if not required:
        return "No required fields specified."
    
    lines = ["REQUIRED FIELDS (must be present in request):"]
    
    for name in sorted(required):
        if name in properties:
            prop = properties[name]
            prop_type = prop.get("type", "any")
            
            # Handle $ref
            if "$ref" in prop:
                ref_name = prop["$ref"].split("/")[-1]
                lines.append(f"  - {name}: {ref_name} (object)")
            elif prop_type == "array":
                items = prop.get("items", {})
                items_type = items.get("type", "object")
                if "$ref" in items:
                    items_type = items["$ref"].split("/")[-1]
                lines.append(f"  - {name}: array of {items_type}")
            else:
                lines.append(f"  - {name}: {prop_type}")
            
            # Add description if short
            desc = prop.get("description", "")
            if desc and len(desc) < 100:
                lines.append(f"      ({desc.split('.')[0]})")
        else:
            lines.append(f"  - {name}: (type not specified)")
    
    return "\n".join(lines)


# Language-specific conventions for dynamic prompt generation
LANGUAGE_CONVENTIONS: Dict[str, Dict[str, Any]] = {
    "python": {
        "display_name": "Python",
        "file_extension": ".py",
        "style_guide": "PEP 8",
        "type_hints": "type hints",
        "docstring_style": "Google-style docstrings",
        "error_pattern": "raise appropriate exceptions (never use bare 'except:', always specify exception type like 'except Exception:' or more specific)",
        "test_framework": "pytest",
        "http_library": "httpx or requests",
        "example_import": "from typing import Dict, Any, Optional",
    },
    "typescript": {
        "display_name": "TypeScript",
        "file_extension": ".ts",
        "style_guide": "ESLint/Prettier",
        "type_hints": "TypeScript types and interfaces",
        "docstring_style": "JSDoc comments",
        "error_pattern": "throw appropriate Error types",
        "test_framework": "Jest or Vitest",
        "http_library": "fetch or axios",
        "example_import": "import type { RequestOptions } from './types';",
    },
    "javascript": {
        "display_name": "JavaScript",
        "file_extension": ".js",
        "style_guide": "ESLint/Prettier",
        "type_hints": "JSDoc type annotations",
        "docstring_style": "JSDoc comments",
        "error_pattern": "throw appropriate Error types",
        "test_framework": "Jest or Vitest",
        "http_library": "fetch or axios",
        "example_import": "const axios = require('axios');",
    },
    "go": {
        "display_name": "Go",
        "file_extension": ".go",
        "style_guide": "Go conventions (gofmt)",
        "type_hints": "Go's static typing",
        "docstring_style": "Go doc comments",
        "error_pattern": "return error values",
        "test_framework": "testing package",
        "http_library": "net/http",
        "example_import": 'import "net/http"',
    },
    "java": {
        "display_name": "Java",
        "file_extension": ".java",
        "style_guide": "Google Java Style",
        "type_hints": "Java's static typing",
        "docstring_style": "Javadoc comments",
        "error_pattern": "throw appropriate exceptions",
        "test_framework": "JUnit",
        "http_library": "HttpClient or OkHttp",
        "example_import": "import java.net.http.HttpClient;",
    },
    "ruby": {
        "display_name": "Ruby",
        "file_extension": ".rb",
        "style_guide": "Ruby Style Guide",
        "type_hints": "YARD type annotations",
        "docstring_style": "YARD documentation",
        "error_pattern": "raise appropriate exceptions",
        "test_framework": "RSpec",
        "http_library": "Faraday or Net::HTTP",
        "example_import": "require 'faraday'",
    },
    "csharp": {
        "display_name": "C#",
        "file_extension": ".cs",
        "style_guide": ".NET coding conventions",
        "type_hints": "C#'s static typing",
        "docstring_style": "XML documentation comments",
        "error_pattern": "throw appropriate exceptions",
        "test_framework": "xUnit or NUnit",
        "http_library": "HttpClient",
        "example_import": "using System.Net.Http;",
    },
}


# =============================================================================
# FEW-SHOT EXAMPLES: Idiomatic code patterns per language
# =============================================================================
# These examples help the LLM generate language-specific idiomatic code.
# Each example demonstrates: HTTP call, error handling, and return type.

LANGUAGE_EXAMPLES: Dict[str, str] = {
    "python": '''
# Python Example - Idiomatic async HTTP client method
async def get_user(self, user_id: str) -> dict:
    """Fetch a user by ID."""
    try:
        response = await self._client.get(f"{self.base_url}/users/{user_id}")
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        raise ApiError(f"Failed to get user: {e.response.status_code}") from e
''',
    
    "typescript": '''
// TypeScript Example - Idiomatic async HTTP client method
async getUser(userId: string): Promise<User> {
  const response = await fetch(`${this.baseUrl}/users/${userId}`, {
    headers: this.getHeaders(),
  });
  
  if (!response.ok) {
    throw new ApiError(`Failed to get user: ${response.status}`);
  }
  
  return response.json() as Promise<User>;
}
''',
    
    "javascript": '''
// JavaScript Example - Idiomatic async HTTP client method
async getUser(userId) {
  const response = await fetch(`${this.baseUrl}/users/${userId}`, {
    headers: this.getHeaders(),
  });
  
  if (!response.ok) {
    throw new Error(`Failed to get user: ${response.status}`);
  }
  
  return response.json();
}
''',
    
    "go": '''
// Go Example - Idiomatic HTTP client method with error handling
func (c *Client) GetUser(ctx context.Context, userID string) (*User, error) {
    url := fmt.Sprintf("%s/users/%s", c.baseURL, userID)
    
    req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
    if err != nil {
        return nil, fmt.Errorf("creating request: %w", err)
    }
    
    resp, err := c.httpClient.Do(req)
    if err != nil {
        return nil, fmt.Errorf("executing request: %w", err)
    }
    defer resp.Body.Close()
    
    if resp.StatusCode != http.StatusOK {
        return nil, fmt.Errorf("unexpected status: %d", resp.StatusCode)
    }
    
    var user User
    if err := json.NewDecoder(resp.Body).Decode(&user); err != nil {
        return nil, fmt.Errorf("decoding response: %w", err)
    }
    
    return &user, nil
}
''',
    
    "java": '''
// Java Example - Idiomatic HTTP client method with exception handling
public User getUser(String userId) throws ApiException {
    try {
        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create(baseUrl + "/users/" + userId))
            .header("Authorization", "Bearer " + apiKey)
            .GET()
            .build();
        
        HttpResponse<String> response = httpClient.send(
            request, HttpResponse.BodyHandlers.ofString()
        );
        
        if (response.statusCode() != 200) {
            throw new ApiException("Failed to get user: " + response.statusCode());
        }
        
        return objectMapper.readValue(response.body(), User.class);
    } catch (IOException | InterruptedException e) {
        throw new ApiException("Request failed", e);
    }
}
''',
    
    "ruby": '''
# Ruby Example - Idiomatic HTTP client method with error handling
def get_user(user_id)
  response = @connection.get("/users/#{user_id}")
  
  unless response.success?
    raise ApiError, "Failed to get user: #{response.status}"
  end
  
  JSON.parse(response.body, symbolize_names: true)
rescue Faraday::Error => e
  raise ApiError, "Request failed: #{e.message}"
end
''',
    
    "csharp": '''
// C# Example - Idiomatic async HTTP client method
public async Task<User> GetUserAsync(string userId, CancellationToken cancellationToken = default)
{
    var response = await _httpClient.GetAsync($"{_baseUrl}/users/{userId}", cancellationToken);
    
    if (!response.IsSuccessStatusCode)
    {
        throw new ApiException($"Failed to get user: {response.StatusCode}");
    }
    
    var content = await response.Content.ReadAsStringAsync(cancellationToken);
    return JsonSerializer.Deserialize<User>(content, _jsonOptions)
        ?? throw new ApiException("Invalid response");
}
''',
}


def get_language_example(language: str) -> str:
    """Get the idiomatic code example for a language."""
    lang_key = language.lower().strip()
    aliases = {"py": "python", "ts": "typescript", "js": "javascript", "golang": "go", "c#": "csharp", "cs": "csharp"}
    lang_key = aliases.get(lang_key, lang_key)
    
    if lang_key in LANGUAGE_EXAMPLES:
        return LANGUAGE_EXAMPLES[lang_key]
        
    return f"""
// {language.title()} Example - Idiomatic HTTP client method
// Please generate idiomatic {language} code following best practices.
// Handle errors, use types if applicable, and document your code.
"""


def get_language_conventions(language: str) -> Dict[str, Any]:
    """
    Get conventions for a target language, with sensible defaults.
    
    Bug #70: This enables dynamic, language-agnostic code generation
    by providing language-specific context to LLM prompts.
    """
    # Normalize language name
    lang_key = language.lower().strip()
    
    # Handle common aliases
    aliases = {
        "py": "python",
        "ts": "typescript",
        "js": "javascript",
        "golang": "go",
        "c#": "csharp",
        "cs": "csharp",
    }
    lang_key = aliases.get(lang_key, lang_key)

    ext_map = {
        "python": ".py",
        "typescript": ".ts",
        "javascript": ".js",
        "go": ".go",
        "java": ".java",
        "ruby": ".rb",
        "csharp": ".cs",
    }

    defaults = {
        "display_name": language.title(),
        "file_extension": ext_map.get(lang_key, f".{lang_key[:3]}") if lang_key else ".txt",
        "style_guide": f"{language.title()} best practices",
        "type_hints": "appropriate type annotations",
        "docstring_style": "standard documentation comments",
        "error_pattern": "handle errors appropriately",
        "test_framework": "appropriate test framework",
        "http_library": "standard HTTP library",
        "example_import": "// imports as needed",
    }

    if lang_key in LANGUAGE_CONVENTIONS:
        merged = defaults.copy()
        merged.update(LANGUAGE_CONVENTIONS[lang_key])
        return merged

    return defaults


# =============================================================================
# Bug #75 Fix: Language-Aware Skeleton Templates
# =============================================================================
# These minimal skeletons provide syntactically correct structure per language.
# Used as fallback when LLM fails, ensuring correct language even on failure.

SKELETON_TEMPLATES = {
    "python": {
        "client": '''"""
{provider_title} API Client

Auto-generated by Integration Co-Worker
"""
from typing import Dict, Any, Optional

# V35-001 Fix: Use pip-installable runtime package
# External repos install: pip install integration-coworker-runtime
from integration_coworker_runtime import IntegrationHttpClient, IntegrationError


class {client_class}(IntegrationHttpClient):
    """Client for {provider_title} API."""
    
    def __init__(
        self,
        base_url: str = "https://api.example.com",
        api_key: Optional[str] = None,
        timeout_s: float = 30.0,
        retries: int = 3,
    ) -> None:
        super().__init__(base_url=base_url, api_key=api_key, timeout_s=timeout_s, retries=retries)

    def {method_name}(self, payload: Optional[Dict[str, Any]] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute the API request for {http_method_upper} {endpoint_path}."""
        try:
            response = self.request(
                method="{http_method_upper}",
                path="{endpoint_path}",
                json=payload,
                params=params,
            )
            return response.json()
        except Exception as exc:  # pragma: no cover - placeholder error handling
            raise IntegrationError(f"Failed to call {http_method_upper} {endpoint_path}: {{exc}}") from exc
''',
        "flow": '''"""
{provider_title} {task_title} Flow

Auto-generated by Integration Co-Worker
"""
import os
from typing import Dict, Any, Optional

from integrations.clients.{client_module} import {client_class}


def {flow_function}(
    payload: Dict[str, Any],
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Execute the {task_title} workflow.

    Args:
        payload: Request payload for the API
        api_key: API authentication key (defaults to env var)
        base_url: API base URL (defaults to env var or client default)
        **kwargs: Additional options forwarded to the client call
        
    Raises:
        ValueError: If api_key is not provided and not in environment
    """
    # V45-003: Get credentials from environment if not provided
    resolved_api_key = api_key or os.environ.get("{provider_title}_API_KEY".upper().replace(" ", "_"))
    if not resolved_api_key:
        raise ValueError("api_key is required (pass directly or set {provider_title}_API_KEY env var)")
    
    # V45-003: Allow base_url override or use client default
    client_kwargs: Dict[str, Any] = {{"api_key": resolved_api_key}}
    if base_url:
        client_kwargs["base_url"] = base_url
    
    client = {client_class}(**client_kwargs)
    response = client.{method_name}(payload=payload, **kwargs)

    return {{
        "success": True,
        "data": response,
    }}
''',
        "test": '''"""
Tests for {provider_title} {task_title}

Auto-generated by Integration Co-Worker
"""
import pytest

def test_{flow_function}_success():
    """Test successful execution."""
    # TODO: Implement test
    assert True

def test_{flow_function}_error_handling():
    """Test error handling."""
    # TODO: Implement test
    assert True
''',
    },
    "typescript": {
        "client": '''/**
 * {provider_title} API Client
 * 
 * Auto-generated by Integration Co-Worker
 */
import axios, {{ AxiosInstance, AxiosResponse }} from 'axios';

export interface {client_class}Config {{
  baseUrl: string;
  apiKey?: string;
}}

export class {client_class} {{
  private client: AxiosInstance;
  
  constructor(config: {client_class}Config) {{
    this.client = axios.create({{
      baseURL: config.baseUrl,
      headers: config.apiKey ? {{ 'Authorization': `Bearer ${{config.apiKey}}` }} : {{}},
    }});
  }}
  
  async {method_name}(params: Record<string, any>): Promise<any> {{
    // TODO: Implement API call
    throw new Error('Method not implemented');
  }}
}}
''',
        "flow": '''/**
 * {provider_title} {task_title} Flow
 * 
 * Auto-generated by Integration Co-Worker
 */
import {{ {client_class} }} from '../clients/{client_module}';

// TODO: Define parameters
}}

export async function {flow_function}(params: {flow_function}Params): Promise<any> {{
  /**
   * Execute the {task_title} workflow.
   * 
   * TODO: Implement workflow logic using the API client.
   */
  throw new Error('Flow not implemented');
}}
''',
        "test": '''/**
 * Tests for {provider_title} {task_title}
 * 
 * Auto-generated by Integration Co-Worker
 */
import {{ describe, it, expect }} from 'vitest';

describe('{flow_function}', () => {{
  it('should execute successfully', async () => {{
    // TODO: Implement test
    expect(true).toBe(true);
  }});
  
  it('should handle errors', async () => {{
    // TODO: Implement test  
    expect(true).toBe(true);
  }});
}});
''',
    },
    "javascript": {
        "client": '''/**
 * {provider_title} API Client
 * 
 * Auto-generated by Integration Co-Worker
 */
const axios = require('axios');

class {client_class} {{
  constructor(config) {{
    this.baseUrl = config.baseUrl;
    this.apiKey = config.apiKey;
    this.client = axios.create({{
      baseURL: this.baseUrl,
      headers: this.apiKey ? {{ 'Authorization': `Bearer ${{this.apiKey}}` }} : {{}},
    }});
  }}
  
  async {method_name}(params) {{
    // TODO: Implement API call
    throw new Error('Method not implemented');
  }}
}}

module.exports = {{ {client_class} }};
''',
        "flow": '''/**
 * {provider_title} {task_title} Flow
 * 
 * Auto-generated by Integration Co-Worker
 */

async function {flow_function}(params) {{
  /**
   * Execute the {task_title} workflow.
   * 
   * TODO: Implement workflow logic using the API client.
   */
  throw new Error('Flow not implemented');
}}

module.exports = {{ {flow_function} }};
''',
        "test": '''/**
 * Tests for {provider_title} {task_title}
 * 
 * Auto-generated by Integration Co-Worker
 */
const {{ {flow_function} }} = require('./{flow_function}');

describe('{flow_function}', () => {{
  test('should execute successfully', async () => {{
    // TODO: Implement test
    expect(true).toBe(true);
  }});
  
  test('should handle errors', async () => {{
    // TODO: Implement test
    expect(true).toBe(true);
  }});
}});
''',
    },
    "go": {
        "client": '''// {provider_title} API Client
//
// Auto-generated by Integration Co-Worker
package client

import (
	"net/http"
)

// {client_class} is the API client for {provider_title}
type {client_class} struct {{
	BaseURL    string
	APIKey     string
	HTTPClient *http.Client
}}

// New{client_class} creates a new API client
func New{client_class}(baseURL, apiKey string) *{client_class} {{
	return &{client_class}{{
		BaseURL:    baseURL,
		APIKey:     apiKey,
		HTTPClient: &http.Client{{}},
	}}
}}

// {method_name} executes the API request
func (c *{client_class}) {method_name}(params map[string]interface{{}}) (map[string]interface{{}}, error) {{
	// TODO: Implement API call
	return nil, nil
}}
''',
        "flow": '''// {provider_title} {task_title} Flow
//
// Auto-generated by Integration Co-Worker
package flow

// {flow_function}Params defines the input parameters
type {flow_function}Params struct {{
	// TODO: Define parameters
}}

// {flow_function} executes the {task_title} workflow
func {flow_function}(params {flow_function}Params) (interface{{}}, error) {{
	// TODO: Implement workflow logic using the API client
	return nil, nil
}}
''',
        "test": '''// Tests for {provider_title} {task_title}
//
// Auto-generated by Integration Co-Worker
package flow

import "testing"

func Test{flow_function}_Success(t *testing.T) {{
	// TODO: Implement test
	t.Log("Test not implemented")
}}

func Test{flow_function}_ErrorHandling(t *testing.T) {{
	// TODO: Implement test
	t.Log("Test not implemented")
}}
''',
    },
    "java": {
        "client": '''/**
 * {provider_title} API Client
 *
 * Auto-generated by Integration Co-Worker
 */
package com.example.client;

import java.net.http.HttpClient;
import java.util.Map;

public class {client_class} {{
    private final String baseUrl;
    private final String apiKey;
    private final HttpClient httpClient;
    
    public {client_class}(String baseUrl, String apiKey) {{
        this.baseUrl = baseUrl;
        this.apiKey = apiKey;
        this.httpClient = HttpClient.newHttpClient();
    }}
    
    public Map<String, Object> {method_name}(Map<String, Object> params) {{
        // TODO: Implement API call
        throw new UnsupportedOperationException("Method not implemented");
    }}
}}
''',
        "flow": '''/**
 * {provider_title} {task_title} Flow
 *
 * Auto-generated by Integration Co-Worker
 */
package com.example.flow;

import java.util.Map;

public class {flow_function}Flow {{
    
    public Map<String, Object> execute(Map<String, Object> params) {{
        // TODO: Implement workflow logic using the API client
        throw new UnsupportedOperationException("Flow not implemented");
    }}
}}
''',
        "test": '''/**
 * Tests for {provider_title} {task_title}
 *
 * Auto-generated by Integration Co-Worker
 */
package com.example.flow;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class {flow_function}FlowTest {{
    
    @Test
    void testSuccess() {{
        // TODO: Implement test
        assertTrue(true);
    }}
    
    @Test
    void testErrorHandling() {{
        // TODO: Implement test
        assertTrue(true);
    }}
}}
''',
    },
    "ruby": {
        "client": '''# {provider_title} API Client
#
# Auto-generated by Integration Co-Worker

require 'faraday'
require 'json'

class {client_class}
  def initialize(base_url:, api_key: nil)
    @base_url = base_url
    @api_key = api_key
    @conn = Faraday.new(url: @base_url)
  end
  
  def {method_name}(**params)
    # TODO: Implement API call
    raise NotImplementedError, 'Method not implemented'
  end
end
''',
        "flow": '''# {provider_title} {task_title} Flow
#
# Auto-generated by Integration Co-Worker

def {flow_function}(**params)
  # Execute the {task_title} workflow.
  #
  # TODO: Implement workflow logic using the API client.
  raise NotImplementedError, 'Flow not implemented'
end
''',
        "test": '''# Tests for {provider_title} {task_title}
#
# Auto-generated by Integration Co-Worker

require 'rspec'

RSpec.describe '{flow_function}' do
  it 'executes successfully' do
    # TODO: Implement test
    expect(true).to be true
  end
  
  it 'handles errors' do
    # TODO: Implement test
    expect(true).to be true
  end
end
''',
    },
    "csharp": {
        "client": '''/// <summary>
/// {provider_title} API Client
///
/// Auto-generated by Integration Co-Worker
/// </summary>
using System;
using System.Net.Http;
using System.Collections.Generic;

namespace Example.Client
{{
    public class {client_class}
    {{
        private readonly string _baseUrl;
        private readonly string _apiKey;
        private readonly HttpClient _httpClient;
        
        public {client_class}(string baseUrl, string apiKey = null)
        {{
            _baseUrl = baseUrl;
            _apiKey = apiKey;
            _httpClient = new HttpClient();
        }}
        
        public Dictionary<string, object> {method_name}(Dictionary<string, object> parameters)
        {{
            // TODO: Implement API call
            throw new NotImplementedException("Method not implemented");
        }}
    }}
}}
''',
        "flow": '''/// <summary>
/// {provider_title} {task_title} Flow
///
/// Auto-generated by Integration Co-Worker
/// </summary>
using System;
using System.Collections.Generic;

namespace Example.Flow
{{
    public class {flow_function}Flow
    {{
        public Dictionary<string, object> Execute(Dictionary<string, object> parameters)
        {{
            // TODO: Implement workflow logic using the API client
            throw new NotImplementedException("Flow not implemented");
        }}
    }}
}}
''',
        "test": '''/// <summary>
/// Tests for {provider_title} {task_title}
///
/// Auto-generated by Integration Co-Worker
/// </summary>
using Xunit;

namespace Example.Tests
{{
    public class {flow_function}FlowTests
    {{
        [Fact]
        public void TestSuccess()
        {{
            // TODO: Implement test
            Assert.True(true);
        }}
        
        [Fact]
        public void TestErrorHandling()
        {{
            // TODO: Implement test
            Assert.True(true);
        }}
    }}
}}
''',
    },
}


def get_skeleton_template(
    language: str,
    artifact_type: str,
    provider_title: str = "API",
    task_title: str = "Task",
    client_class: str = "ApiClient",
    client_module: str = "client",
    method_name: str = "execute",
    flow_function: str = "execute_flow",
    endpoint_path: Optional[str] = None,
    http_method: Optional[str] = None,
) -> str:
    """
    Get a language-appropriate skeleton template for fallback.
    
    Bug #75 Fix: Returns syntactically correct code in the target language
    instead of Python code labeled as another language.
    
    Args:
        language: Target language (python, typescript, go, etc.)
        artifact_type: Type of artifact (client, flow, test)
        provider_title: Human-readable provider name
        task_title: Human-readable task name
        client_class: Name for the client class
        method_name: Name for the primary method
        flow_function: Name for the flow function
        
    Returns:
        Skeleton code in the target language
    """
    # Normalize language and add client_module parameter
    lang_key = language.lower().strip()
    aliases = {
        "py": "python",
        "ts": "typescript",
        "js": "javascript",
        "golang": "go",
        "c#": "csharp",
        "cs": "csharp",
    }
    lang_key = aliases.get(lang_key, lang_key)
    
    # Get templates for this language, fallback to generic if unknown
    # Bug #75 Fix: Use generic skeleton for unknown languages instead of Python
    if lang_key not in SKELETON_TEMPLATES:
        return _get_generic_skeleton(language, artifact_type, provider_title, task_title, client_class, method_name, flow_function)
        
    lang_templates = SKELETON_TEMPLATES.get(lang_key)
    template = lang_templates.get(artifact_type, lang_templates.get("client", ""))

    # Normalize endpoint details for inline hints
    endpoint_path = endpoint_path or "/"
    method_upper = (http_method or "GET").upper()
    method_lower = method_upper.lower()
    
    # Format template with provided values
    # Handle Go method naming (capitalize first letter for exported)
    go_method = method_name[0].upper() + method_name[1:] if method_name else "Execute"
    go_flow = flow_function[0].upper() + flow_function[1:] if flow_function else "ExecuteFlow"
    
    return template.format(
        provider_title=provider_title,
        task_title=task_title,
        client_class=client_class,
        client_module=client_module,
        method_name=go_method if lang_key == "go" else method_name,
        flow_function=go_flow if lang_key == "go" else flow_function,
        endpoint_path=endpoint_path,
        http_method_upper=method_upper,
        http_method_lower=method_lower,
    )


def _get_generic_skeleton(
    language: str,
    artifact_type: str,
    provider_title: str,
    task_title: str,
    client_class: str,
    method_name: str,
    flow_function: str,
) -> str:
    """Generate a generic skeleton for unknown languages."""
    if artifact_type == "client":
        return f"""// {provider_title} API Client in {language}
// Auto-generated by Integration Co-Worker

// TODO: Define class/struct {client_class}
// TODO: Implement method {method_name}
// TODO: Handle authentication and HTTP requests
"""
    elif artifact_type == "flow":
        return f"""// {provider_title} {task_title} Flow in {language}
// Auto-generated by Integration Co-Worker

// TODO: Define function {flow_function}
// TODO: Implement workflow logic
"""
    else:
        return f"""// Tests for {provider_title} {task_title} in {language}
// Auto-generated by Integration Co-Worker

// TODO: Implement tests for {flow_function}
"""


def _build_task_function_constraints(
    task_function_name: Optional[str] = None,
    task_function_signature: Optional[str] = None,
    artifact_kind: str = "flow",
) -> str:
    """
    Build constraint text for task-extracted function requirements.
    
    V37-002 Fix: When users specify explicit function names or signatures in their
    task description (e.g., "create function enhance_docstring(original: str) -> str"),
    we need to ensure the LLM honors those requirements instead of using generic names.
    
    Args:
        task_function_name: User-specified function name (e.g., "enhance_docstring")
        task_function_signature: User-specified signature (e.g., "(original: str, code: str) -> str")
        artifact_kind: Type of artifact ("client", "flow", "test")
        
    Returns:
        Constraint text to add to prompt, or "None specified" if no constraints
    """
    if not task_function_name and not task_function_signature:
        return "None specified - use the skeleton's function name."
    
    constraints = []
    
    if task_function_name:
        constraints.append(
            f"⚠️  CRITICAL: The user explicitly requested the function be named `{task_function_name}`.\n"
            f"   - DO NOT use generic names like `*_flow` or `*_with_llm_flow`\n"
            f"   - The PRIMARY exported function MUST be named EXACTLY: `{task_function_name}`\n"
            f"   - You may have internal helper functions with other names, but the main API function must match"
        )
    
    if task_function_signature:
        constraints.append(
            f"⚠️  CRITICAL: The user specified this exact function signature: `{task_function_signature}`\n"
            f"   - Match the parameter names and types EXACTLY\n"
            f"   - Match the return type EXACTLY\n"
            f"   - Do NOT add extra required parameters like `api_key` unless they're in the signature"
        )
    
    # Add artifact-specific guidance
    if artifact_kind == "flow" and task_function_name:
        constraints.append(
            f"\nFor the FLOW artifact specifically:\n"
            f"   - The flow's entry point function MUST be `{task_function_name}`\n"
            f"   - Internal implementation can call the client, but the PUBLIC API is `{task_function_name}`"
        )
    elif artifact_kind == "test" and task_function_name:
        constraints.append(
            f"\nFor the TEST artifact specifically:\n"
            f"   - Tests should import and test `{task_function_name}` (not a generic flow name)\n"
            f"   - Mock the client, but call `{task_function_name}` directly"
        )
    
    return "\n\n".join(constraints)


def _build_async_instructions(is_async_required: bool, target_language: str) -> str:
    """
    V38-007: Build async/concurrent code generation instructions.
    
    When the task description indicates async/concurrent requirements, this
    provides specific guidance to the LLM for generating proper async code.
    
    Args:
        is_async_required: Whether async code patterns should be generated
        target_language: Target language (python, javascript, etc.)
        
    Returns:
        Instruction string for async code generation, or empty string if not required
    """
    if not is_async_required:
        return ""
    
    lang = target_language.lower()
    
    if lang in ("python", "py"):
        return """
## ASYNC/CONCURRENT CODE REQUIREMENTS
⚠️  CRITICAL: The task requires ASYNC/CONCURRENT code patterns.

For Python, you MUST:
1. Use `async def` for functions that perform I/O operations
2. Use `await` for calling async functions
3. Import `asyncio` for concurrency primitives
4. For batch/parallel operations, use `asyncio.gather()` or `asyncio.create_task()`
5. The client should use async HTTP (e.g., `httpx.AsyncClient` or `aiohttp.ClientSession`)
6. For tests, use `pytest.mark.asyncio` and async test functions

Example async pattern:
```python
import asyncio
from typing import List

async def process_batch_async(items: List[str]) -> List[Result]:
    \"\"\"Process multiple items concurrently.\"\"\"
    tasks = [process_single_async(item) for item in items]
    results = await asyncio.gather(*tasks)
    return results
```

DO NOT generate synchronous code when async is required.
"""
    elif lang in ("javascript", "js", "typescript", "ts"):
        return """
## ASYNC/CONCURRENT CODE REQUIREMENTS
⚠️  CRITICAL: The task requires ASYNC/CONCURRENT code patterns.

For JavaScript/TypeScript, you MUST:
1. Use `async function` or `async () =>` for async functions
2. Use `await` for calling async functions
3. For batch/parallel operations, use `Promise.all()` or `Promise.allSettled()`
4. Return Promises from async operations
5. Handle errors with try/catch or .catch()

Example async pattern:
```typescript
async function processBatchAsync(items: string[]): Promise<Result[]> {
  const promises = items.map(item => processSingleAsync(item));
  const results = await Promise.all(promises);
  return results;
}
```

DO NOT generate synchronous code when async is required.
"""
    else:
        # Generic async guidance for other languages
        return """
## ASYNC/CONCURRENT CODE REQUIREMENTS
⚠️  CRITICAL: The task requires ASYNC/CONCURRENT code patterns.

Generate code using your language's idiomatic async/concurrent patterns:
- Use async/await constructs if available
- Use concurrent execution for batch operations
- Ensure proper error handling for concurrent operations
- Avoid blocking I/O operations

DO NOT generate purely synchronous/sequential code when async is required.
"""


def _build_streaming_instructions(is_streaming_required: bool, target_language: str) -> str:
    """
    V38-008: Build streaming response handling instructions.
    
    When the task description indicates streaming requirements, this
    provides specific guidance to the LLM for generating proper streaming code.
    
    The key issue this fixes: LLMs often generate broken streaming code that
    tries to use response objects incorrectly (e.g., calling .json() on a 
    streaming response or using context managers incorrectly).
    
    Args:
        is_streaming_required: Whether streaming code patterns should be generated
        target_language: Target language (python, javascript, etc.)
        
    Returns:
        Instruction string for streaming code generation, or empty string if not required
    """
    if not is_streaming_required:
        return ""
    
    lang = target_language.lower()
    
    if lang in ("python", "py"):
        return """
## STREAMING RESPONSE REQUIREMENTS
⚠️  CRITICAL: The task requires STREAMING response handling.

For Python streaming with `requests` library:
```python
import requests
from typing import Iterator, Generator

def stream_response(url: str, headers: dict, payload: dict) -> Generator[str, None, None]:
    \"\"\"Stream response chunks from API.\"\"\"
    with requests.post(url, headers=headers, json=payload, stream=True) as response:
        response.raise_for_status()
        for line in response.iter_lines(decode_unicode=True):
            if line:
                # For SSE: lines often start with "data: "
                if line.startswith("data: "):
                    data = line[6:]  # Remove "data: " prefix
                    if data != "[DONE]":
                        yield data
                else:
                    yield line
```

CRITICAL STREAMING RULES:
1. Use `stream=True` parameter when making the request
2. Use `with` context manager for proper resource cleanup
3. Use `response.iter_lines()` or `response.iter_content()` to iterate chunks
4. DO NOT call `response.json()` on a streaming response - it will fail!
5. DO NOT try to access `response.status_code` outside the context manager
6. For Server-Sent Events (SSE), parse the "data: " prefix from each line
7. Handle the "[DONE]" sentinel that indicates stream completion

WRONG - This will cause errors:
```python
# WRONG: response.json() doesn't work with streaming
response = requests.post(url, stream=True)
data = response.json()  # ERROR!

# WRONG: accessing response outside context manager
with requests.get(url, stream=True) as response:
    pass
print(response.status_code)  # ERROR - response may be closed!
```

For `httpx` (async streaming):
```python
import httpx

async def stream_response_async(url: str, headers: dict, payload: dict):
    async with httpx.AsyncClient() as client:
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line and line.startswith("data: "):
                    yield line[6:]
```
"""
    elif lang in ("javascript", "js", "typescript", "ts"):
        return """
## STREAMING RESPONSE REQUIREMENTS
⚠️  CRITICAL: The task requires STREAMING response handling.

For JavaScript/TypeScript streaming with fetch:
```typescript
async function* streamResponse(url: string, payload: object): AsyncGenerator<string> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  if (!response.body) throw new Error('No response body');
  
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    
    const chunk = decoder.decode(value);
    for (const line of chunk.split('\\n')) {
      if (line.startsWith('data: ') && line !== 'data: [DONE]') {
        yield line.slice(6);
      }
    }
  }
}
```

CRITICAL: Use ReadableStream and getReader() for streaming, NOT response.json()!
"""
    else:
        return """
## STREAMING RESPONSE REQUIREMENTS
⚠️  CRITICAL: The task requires STREAMING response handling.

Generate code using your language's idiomatic streaming patterns:
- Enable streaming mode on HTTP requests
- Iterate over response chunks/lines as they arrive
- DO NOT try to parse the entire response as JSON at once
- Handle Server-Sent Events (SSE) format if applicable
- Properly close/cleanup resources after streaming
"""


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
    target_language: Optional[str] = None,
    # V37-002: Task-extracted function requirements
    task_function_name: Optional[str] = None,
    task_function_signature: Optional[str] = None,
    # V38-007: Async code generation flag
    is_async_required: bool = False,
    # V38-008: Streaming response handling flag
    is_streaming_required: bool = False,
) -> str:
    """
    Build a comprehensive prompt for LLM code generation.
    
    This prompt includes:
    - Task and provider context
    - Endpoint details (method, path, schemas)
    - Target repo style from RepoProfile
    - Skeleton code to fill in
    - Non-negotiable constraints
    
    Bug #70 (v3): Added target_language parameter for dynamic language support.
    When target_language is provided, prompts are generated for that language.
    Otherwise, defaults to repo_profile.language or Python.
    
    V38-007: Added is_async_required for async/concurrent code generation.
    V38-008: Added is_streaming_required for streaming response handling.
    
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
        target_language: Target programming language (default: from repo_profile or "python")
    
    Returns:
        A detailed prompt string for the LLM
    """
    provider_code = state.provider_code or "unknown"
    
    # Bug #70: Determine target language dynamically
    if target_language:
        lang = target_language
    elif repo_profile and repo_profile.language:
        lang = repo_profile.language
    else:
        lang = "python"
    
    # Get language-specific conventions
    conventions = get_language_conventions(lang)
    
    # v2: Sanitize task description to prevent prompt injection (SEC-002)
    raw_task_desc = task.description if task else state.task_description or ""
    task_desc = sanitize_task_description(raw_task_desc) or "API integration"

    # Build endpoint context
    endpoint_context = _build_endpoint_context(endpoint)

    # Build repo style context (now includes language from conventions)
    repo_context = _build_repo_context(repo_profile, conventions)

    # Build artifact-specific instructions (now language-aware)
    artifact_instructions = _build_artifact_instructions(
        artifact_kind=artifact_kind,
        client_class=client_class,
        method_name=method_name,
        flow_function=flow_function,
        conventions=conventions,
    )

    # Build schema context if available
    schema_context = _build_schema_context(state, endpoint)

    # v2.2 SEC-004: Build valid paths context to prevent hallucination
    valid_paths_context = _build_valid_paths_context(state)

    # Bug #70: Dynamic language-aware prompt
    lang_name = conventions["display_name"]
    style_guide = conventions["style_guide"]
    type_hints = conventions["type_hints"]
    error_pattern = conventions["error_pattern"]
    
    # Determine code fence language for skeleton
    code_fence_lang = lang.lower()
    
    # Get idiomatic code example for the target language
    language_example = get_language_example(lang)
    
    # V37-002: Build task function constraints if user specified explicit requirements
    task_function_constraints = _build_task_function_constraints(
        task_function_name=task_function_name,
        task_function_signature=task_function_signature,
        artifact_kind=artifact_kind,
    )
    
    # V38-007: Build async code generation instructions
    async_instructions = _build_async_instructions(is_async_required, lang) if is_async_required else ""
    
    # V38-008: Build streaming response handling instructions
    streaming_instructions = _build_streaming_instructions(is_streaming_required, lang) if is_streaming_required else ""

    prompt = f"""You are a senior {lang_name} developer generating production-quality API integration code.

## TASK
Provider: {provider_code}
Task: {task_desc}
Artifact Type: {artifact_kind}
Target Language: {lang_name}

## ENDPOINT DETAILS
{endpoint_context}

## VALID API PATHS
{valid_paths_context}

## REQUEST/RESPONSE SCHEMAS
{schema_context}

## TARGET REPOSITORY STYLE
{repo_context}

## ARTIFACT REQUIREMENTS
{artifact_instructions}

## SKELETON CODE
Fill in the function/class bodies in this skeleton. Keep the imports, class name, method name, and signatures EXACTLY as provided.
DO NOT add extra test methods, helper functions, or classes beyond what's in the skeleton.

```{code_fence_lang}
{skeleton_code}
```

## TASK-SPECIFIC FUNCTION REQUIREMENTS
{task_function_constraints}
{async_instructions}
{streaming_instructions}
## CONSTRAINTS (MUST FOLLOW)
1. Return ONLY valid {lang_name} code, no markdown fences or explanations
2. Keep all imports, class names, method names, and function signatures exactly as provided
3. DO NOT add new test methods, classes, or functions beyond the skeleton
4. Add comprehensive documentation using {conventions["docstring_style"]}
5. Use {type_hints} throughout
6. Include proper error handling ({error_pattern})
7. Do NOT hardcode any URLs, API keys, or test data
8. Follow {style_guide} conventions
9. For tests: ALL flow calls MUST be inside `with patch(...)` context managers - no unmocked calls

## OUTPUT
Return the complete, refined {lang_name} code with filled-in function bodies:
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


def _build_valid_paths_context(state: WorkflowState) -> str:
    """
    Build context string listing all valid API paths from the spec.
    
    This helps prevent LLM hallucination by providing an explicit numbered list
    of valid paths. Using numbers makes it easier for LLMs to reference exact paths
    without inventing variations (SEC-004 v2).
    
    Bug #30 Fix: Changed to numbered list format with stronger constraints.
    Bug #89 Fix: For large specs (>100 endpoints), filter by task relevance.
    Per PRODUCTION_AUDIT_DEC12.md - Path hallucination prevention.
    """
    if not state.endpoints:
        return ""
    
    # Bug #89: For large specs, filter endpoints by task relevance
    max_endpoints_per_method = 25
    total_endpoints = len(state.endpoints)
    
    endpoints_to_show = state.endpoints
    task_keywords = set()
    is_filtered = False
    
    if total_endpoints > 100:
        # Extract keywords from task description
        if state.task_description:
            for word in state.task_description.lower().split():
                clean = ''.join(c for c in word if c.isalnum())
                if len(clean) > 2:
                    task_keywords.add(clean)
        
        # Score endpoints by relevance
        scored = []
        for ep in state.endpoints:
            path_lower = ep.path.lower()
            desc_lower = (ep.description or "").lower() if hasattr(ep, 'description') else ""
            op_lower = (ep.operation_id or "").lower() if hasattr(ep, 'operation_id') else ""
            
            score = 0
            for kw in task_keywords:
                if kw in path_lower:
                    score += 3
                if kw in op_lower:
                    score += 2
                if kw in desc_lower:
                    score += 1
            
            scored.append((score, ep))
        
        # Sort by score, take top 100 most relevant
        scored.sort(key=lambda x: (-x[0], x[1].path))
        endpoints_to_show = [ep for _, ep in scored[:100]]
        is_filtered = True
    
    # Group paths by method for better readability
    paths_by_method: dict[str, list[tuple[str, int]]] = {}
    for ep in endpoints_to_show:
        method = ep.method.upper() if ep.method else "UNKNOWN"
        if method not in paths_by_method:
            paths_by_method[method] = []
        # Include relevance score for marking
        score = 0
        if task_keywords:
            for kw in task_keywords:
                if kw in ep.path.lower():
                    score += 1
        paths_by_method[method].append((ep.path, score))
    
    lines = [
        "="*60,
        "⚠️  VALID API PATHS - EXACT STRINGS ONLY ⚠️",
        "="*60,
        "",
        "CRITICAL: Copy paths EXACTLY as shown. Do NOT:",
        "  ❌ Modify capitalization ('/alphasenders' vs '/AlphaSenders')",
        "  ❌ Add or remove path segments",
        "  ❌ Invent paths that seem reasonable",
        "  ❌ Guess based on similar patterns",
        "",
    ]
    
    if is_filtered:
        lines.append(f"(Showing {len(endpoints_to_show)} of {total_endpoints} endpoints, filtered by task relevance)")
        if task_keywords:
            lines.append(f"(Task keywords: {', '.join(list(task_keywords)[:5])})")
        lines.append("")
    
    path_num = 1
    for method in sorted(paths_by_method.keys()):
        path_tuples = paths_by_method[method]
        # Sort by score (descending) then path
        path_tuples.sort(key=lambda x: (-x[1], x[0]))
        lines.append(f"### {method} endpoints:")
        # Limit to first 25 paths per method to avoid prompt bloat
        display_paths = path_tuples[:max_endpoints_per_method]
        for path, score in display_paths:
            # Add quotes to make exact string clear
            marker = " ★" if score > 0 else ""
            lines.append(f'  [{path_num}] "{path}"{marker}')
            path_num += 1
        if len(path_tuples) > max_endpoints_per_method:
            lines.append(f"  ... and {len(path_tuples) - max_endpoints_per_method} more {method} endpoints")
        lines.append("")
    
    lines.extend([
        "="*60,
        "## PATH RULES (VIOLATIONS CAUSE GENERATION FAILURE):",
        "",
        "1. ✅ ONLY use paths that appear in the numbered list above (★ = relevant to task)",
        "2. ✅ Copy the path string EXACTLY - character for character",
        "3. ✅ If uncertain, use ONLY the primary endpoint from ENDPOINT DETAILS",
        "",
        "Common hallucination patterns to AVOID:",
        "  ❌ '/AlphaSenders' - invented path segment",
        "  ❌ '/Compliance/Usa2p/' - invented compliance path",
        "  ❌ '/v2/...' - changing API version",
        "  ❌ Adding '/list', '/create', '/update' suffixes",
        "",
        "If the path you need isn't listed above, DO NOT use it.",
        "="*60,
        "",
    ])
    return "\n".join(lines)


def _build_repo_context(
    repo_profile: Optional[RepoProfile],
    conventions: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Build context string for the repo profile.
    
    Bug #70: Now accepts conventions for language-aware defaults.
    """
    if not conventions:
        conventions = get_language_conventions("python")
    
    if not repo_profile:
        return f"Standard {conventions['display_name']} package structure."

    lines = [
        f"Framework: {repo_profile.framework or 'generic'}",
        f"Language: {repo_profile.language or conventions['display_name']}",
        f"HTTP Library: {conventions.get('http_library', 'standard HTTP library')}",
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
    conventions: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Build artifact-specific instructions.
    
    Bug #70: Now language-aware via conventions parameter.
    """
    if not conventions:
        conventions = get_language_conventions("python")
    
    lang_name = conventions["display_name"]
    error_pattern = conventions["error_pattern"]
    test_framework = conventions["test_framework"]
    
    if artifact_kind == "client":
        # Bug #93 Fix: Add explicit instructions about rate limit lock typing
        # Bug #94 Fix: Add explicit instructions about Response type handling
        return f"""Generate an API client class in {lang_name}.
- Class name must be: {client_class}
- Main method must be: {method_name}
- The method should accept a payload (dict/object) and optional idempotency_key
- Handle HTTP errors appropriately ({error_pattern})
- Support configurable base_url via constructor
- Use {conventions['http_library']} for HTTP requests

CRITICAL TYPE SAFETY RULES (Bug #93/94 - MUST follow for mypy):
- If the skeleton has _rate_limit_lock: Optional[threading.Lock] = None, keep it Optional
- NEVER reassign _rate_limit_lock = None after it's been set to a Lock()
- The lock should only be initialized once in _init_rate_limiter
- DO NOT add any code that sets the lock back to None
- DO NOT narrow the type of _rate_limit_lock by removing Optional

CRITICAL HTTP RESPONSE RULES (Bug #94 - MUST follow for mypy):
- The self.request() method ALWAYS returns a Response, NEVER returns None
- DO NOT type hint response as Optional[Response] or Response | None
- Correct: response: Response = self.request(...) or just response = self.request(...)
- WRONG: response: Optional[Response] = self.request(...)
- You can access response.status_code, response.text, response.json() directly
- NO need to check "if response is None" - the request() method raises on failure
- If the request fails, it raises IntegrationError, not returns None"""

    elif artifact_kind == "flow":
        # Bug #90 Fix: Don't add extra payload field validation  
        # Bug #92 Fix: Don't add extra required parameters beyond api_key and payload
        return f"""Generate a workflow/flow function in {lang_name}.
- Function name must be: {flow_function}
- Accept api_key, payload, and additional options via **kwargs
- Transform and return the API response
- Handle API errors via the client

CRITICAL VALIDATION RULES (Bug #92 - MUST follow):
- The ONLY validation checks should be for empty api_key and empty payload
- Keep EXACTLY these two checks from the skeleton:
    if not api_key: raise ValueError("api_key is required")
    if not payload: raise ValueError("payload is required")
- DO NOT add additional required parameter checks (owner, repo, service_sid, etc.)
- DO NOT add validation for specific keys inside the payload
- DO NOT check kwargs for required fields
- Any endpoint-specific fields should be OPTIONAL in kwargs, not required
- Let the API return errors for missing/invalid endpoint-specific fields
- The test suite expects ONLY api_key and payload validation errors

IMPORTANT: If the API requires extra parameters (owner, repo, etc.), pass them via
**kwargs or extract from payload, but DO NOT raise ValueError for missing ones."""

    elif artifact_kind == "test":
        # Bug #31 Fix: Add explicit credential handling instructions (now language-aware)
        # Bug #90 Fix: Add explicit safe assertion patterns to prevent call_args issues
        # Bug #91 Fix: EVERY test must use mocks - no real HTTP calls allowed
        # Bug #92 Fix: Tests align exactly with flow validation (api_key, payload only)
        return f"""Generate {test_framework} test cases in {lang_name}.
- Use {test_framework} and mocking utilities
- Mock the API client to avoid real network calls
- Test both success and error scenarios
- Use meaningful test data that reflects the API
- Test input validation

CRITICAL TEST STRUCTURE RULES (Bug #91/92 - MUST follow):
- DO NOT add extra test methods beyond what's in the skeleton
- The skeleton provides exactly 3 test methods - fill them in but DO NOT add more
- The flow only validates api_key and payload - test ONLY those validations
- DO NOT test for missing owner, repo, service_sid, or other endpoint params

CRITICAL TEST/FLOW ALIGNMENT (Bug #92):
- test_success: Call flow with api_key="test_key", payload={{"key": "value"}}
- test_missing_api_key: Expect ValueError("api_key is required") with api_key=""
- test_missing_payload: Expect ValueError("payload is required") with payload={{}} or None
- The flow MUST raise these exact errors - no other validation errors

CRITICAL MOCKING RULES (Bug #91 - MUST follow):
- EVERY test method MUST mock the client class with: with patch('module.ClientClass') as MockClient
- NEVER call the flow function without an active mock patch
- ALL HTTP calls MUST be mocked - real network calls will FAIL in sandbox
- Even validation error tests MUST have an active mock before calling the flow
- DO NOT add extra test methods that skip mocking

CRITICAL CREDENTIAL RULES (must follow):
- NEVER hardcode API keys, tokens, or credentials in test code
- Use environment variables or test fixtures for credentials
- Use placeholder strings like "test_api_key" ONLY for mocked tests where the actual value doesn't matter
- For test data, use clearly fake values like "test_123" not realistic-looking keys

CRITICAL ASSERTION RULES (Bug #90 - must follow):
- DO NOT use call_args[0] or call_args[1] to check mock arguments - these cause KeyError
- DO use mock.method.called to verify the mock was called
- DO use mock.method.call_count >= 1 to verify call counts
- DO use isinstance(result, dict) or "key" in result for dict checks
- DO use assert result is not None for existence checks
- DO NOT access nested dictionary keys like call_args[1]["key"] - this breaks tests
- Keep assertions simple: check if mock was called, check result type, check basic structure

CRITICAL FUNCTION SIGNATURE:
- The flow function signature is: flow_function(api_key: str, payload: Dict[str, Any], **kwargs)
- ALL test calls to the flow MUST pass both api_key AND payload parameters
- Example: flow_function(api_key="test_key", payload={{"key": "value"}})"""

    return f"Generate clean, well-documented {lang_name} code."


def _build_schema_context(state: WorkflowState, endpoint: Optional[Endpoint]) -> str:
    """
    Build context string for request/response schemas.
    
    V38-006: Enhanced to include request body examples and required field structure,
    which helps the LLM generate correct request payloads.
    """
    from integration_coworker.codegen.paths import get_openapi_spec_dict
    
    spec = get_openapi_spec_dict(state)
    if not spec:
        return "Schema details not available."

    lines = []

    # Try to find request body schema
    if endpoint:
        paths = spec.get("paths", {})
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
                lines.append(_format_schema(schema, spec))
                
                # V38-006: Add required fields summary
                lines.append("")
                lines.append(_format_required_structure(schema, spec))
                
                # V38-006: Add concrete request example
                example = _extract_request_example(operation, spec)
                if example:
                    lines.append("")
                    lines.append("EXAMPLE REQUEST BODY:")
                    lines.append("```json")
                    lines.append(json.dumps(example, indent=2))
                    lines.append("```")

        # Response schema
        responses = operation.get("responses", {})
        success_response = responses.get("200", responses.get("201", {}))
        if success_response:
            content = success_response.get("content", {})
            json_content = content.get("application/json", {})
            schema = json_content.get("schema", {})
            if schema:
                lines.append("\nResponse Schema:")
                lines.append(_format_schema(schema, spec))

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


def build_constrained_body_prompt(
    endpoint_path: str,
    http_method: str,
    base_url: str,
    provider_code: str,
    client_class: str,
    method_name: str,
    request_schema: Optional[Dict[str, Any]] = None,
    response_schema: Optional[Dict[str, Any]] = None,
    summary: Optional[str] = None,
    spec: Optional[Dict[str, Any]] = None,
    required_headers: Optional[Dict[str, str]] = None,
) -> str:
    """
    Build a prompt for constrained code generation.
    
    V2.2 (Dynamic Capability Fix #1): Constrained generation injects FIXED paths 
    from the spec and asks the LLM to only fill in the method body logic.
    This prevents path hallucination (Bug #29, #30).
    
    V38-005: Added required_headers parameter for beta endpoints like OpenAI Assistants API.
    
    The LLM cannot hallucinate paths because:
    1. The path is already hardcoded in the skeleton
    2. The prompt explicitly tells the LLM NOT to modify the path
    3. We validate the output still contains the exact path
    
    Args:
        endpoint_path: The EXACT path from the spec (e.g., "/v1/checkout/sessions")
        http_method: HTTP method (GET, POST, etc.)
        base_url: API base URL
        provider_code: Provider identifier (e.g., "stripe")
        client_class: Name of the client class to generate
        method_name: Name of the method to generate
        request_schema: Optional request body schema
        response_schema: Optional response schema
        summary: Optional endpoint summary/description
        spec: Optional full OpenAPI spec for schema resolution
        required_headers: Optional dict of required headers (V38-005)
    
    Returns:
        A prompt string that constrains the LLM to only fill method bodies
    """
    # Format schemas if available
    request_schema_str = "No request body required."
    required_fields_str = ""
    request_example_str = ""
    
    if request_schema and spec:
        request_schema_str = _format_schema(request_schema, spec)
        # V38-006: Add required fields structure
        required_fields_str = _format_required_structure(request_schema, spec)
        # V38-006: Generate example request body
        example = _generate_minimal_example(request_schema, spec)
        if example:
            request_example_str = f"\nEXAMPLE REQUEST BODY:\n```json\n{json.dumps(example, indent=2)}\n```"
    elif request_schema:
        request_schema_str = str(request_schema)
    
    response_schema_str = "Response is a JSON object."
    if response_schema and spec:
        response_schema_str = _format_schema(response_schema, spec)
    elif response_schema:
        response_schema_str = str(response_schema)
    
    endpoint_desc = summary or f"{http_method.upper()} {endpoint_path}"
    
    # Determine body handling based on method
    body_param = ""
    body_arg = ""
    if http_method.upper() in ("POST", "PUT", "PATCH"):
        body_param = ",\n        payload: Dict[str, Any],"
        body_arg = "\n            json=payload,"
    
    # V38-005: Build required headers lines for beta endpoints
    required_headers_lines = ""
    if required_headers:
        for header_name, header_value in required_headers.items():
            required_headers_lines += f'\n        headers["{header_name}"] = "{header_value}"'
    
    prompt = f"""You are a senior Python developer. Fill in ONLY the method body for the following API client.

## CONTEXT
Provider: {provider_code}
Endpoint: {endpoint_desc}
HTTP Method: {http_method.upper()}
Path: {endpoint_path}

## REQUEST SCHEMA
{request_schema_str}

{required_fields_str}
{request_example_str}

## RESPONSE SCHEMA
{response_schema_str}

## SKELETON (DO NOT MODIFY STRUCTURE)
The path and URL construction are ALREADY CORRECT. Do NOT change them.
Fill in ONLY the implementation logic within the marked sections.

```python
class {client_class}:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "{base_url}",
    ):
        self.api_key = api_key or os.environ.get("{provider_code.upper()}_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=30.0)
    
    def {method_name}(
        self{body_param}
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        # FIXED PATH - DO NOT MODIFY
        url = f"{{self.base_url}}{endpoint_path}"
        
        headers = {{}}
        if self.api_key:
            headers["Authorization"] = f"Bearer {{self.api_key}}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key{required_headers_lines}
        
        # ===== FILL IN BELOW =====
        # Make the HTTP request and handle the response
        response = self._client.request(
            "{http_method.upper()}",
            url,
            headers=headers,{body_arg}
        )
        
        # TODO: Add error handling and response parsing
        # ===== END FILL IN =====
        
        return {{}}
```

## INSTRUCTIONS
1. Fill in the TODO section with proper error handling and response parsing
2. DO NOT modify the path variable or URL construction - it is already correct
3. DO NOT add any additional endpoint paths or methods
4. Keep the class structure exactly as shown
5. Return ONLY the complete Python code, no markdown fences

## OUTPUT
Return the complete Python code with the method body filled in:
"""
    return prompt
