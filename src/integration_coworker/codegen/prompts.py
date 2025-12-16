"""
LLM prompt builders for code generation.

Builds rich, context-aware prompts that help the LLM generate
high-quality, spec-driven, repo-fitting code.

v2: Implements FT-SEC-002 - Input sanitization for task descriptions
v3 (Bug #70): Dynamic language support - prompts adapt to target language
"""
from typing import Literal, Optional, Dict, Any

from integration_coworker.domain.models import Endpoint, IntegrationTask
from integration_coworker.repo.models import RepoProfile
from integration_coworker.graph.state import WorkflowState
from integration_coworker.llm.sanitizer import sanitize_task_description


# Language-specific conventions for dynamic prompt generation
LANGUAGE_CONVENTIONS: Dict[str, Dict[str, Any]] = {
    "python": {
        "display_name": "Python",
        "file_extension": ".py",
        "style_guide": "PEP 8",
        "type_hints": "type hints",
        "docstring_style": "Google-style docstrings",
        "error_pattern": "raise appropriate exceptions",
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
        "rs": "rust",
        "cpp": "cpp",
        "c++": "cpp",
    }
    lang_key = aliases.get(lang_key, lang_key)
    
    if lang_key in LANGUAGE_CONVENTIONS:
        return LANGUAGE_CONVENTIONS[lang_key]
    
    # Better extension guessing
    ext_map = {
        "rust": ".rs",
        "cpp": ".cpp",
        "c": ".c",
        "swift": ".swift",
        "kotlin": ".kt",
        "scala": ".scala",
        "php": ".php",
        "lua": ".lua",
        "perl": ".pl",
        "r": ".r",
        "dart": ".dart",
        "elixir": ".ex",
        "haskell": ".hs",
    }
    
    # Return generic defaults for unknown languages
    return {
        "display_name": language.title(),
        "file_extension": ext_map.get(lang_key, f".{lang_key[:3]}"),
        "style_guide": f"{language.title()} best practices",
        "type_hints": "appropriate type annotations",
        "docstring_style": "standard documentation comments",
        "error_pattern": "handle errors appropriately",
        "test_framework": "appropriate test framework",
        "http_library": "standard HTTP library",
        "example_import": "// imports as needed",
    }


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
import httpx

class {client_class}:
    """Client for {provider_title} API."""
    
    def __init__(self, base_url: str, api_key: Optional[str] = None):
        self.base_url = base_url
        self.api_key = api_key
        self._client = httpx.Client()
    
    def {method_name}(self, **kwargs) -> Dict[str, Any]:
        """Execute the API request."""
        # TODO: Implement API call
        raise NotImplementedError("Method not implemented")
''',
        "flow": '''"""
{provider_title} {task_title} Flow

Auto-generated by Integration Co-Worker
"""
from typing import Dict, Any

def {flow_function}(**kwargs) -> Dict[str, Any]:
    """
    Execute the {task_title} workflow.
    
    TODO: Implement workflow logic using the API client.
    """
    raise NotImplementedError("Flow not implemented")
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

export interface {flow_function}Params {{
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
    method_name: str = "execute",
    flow_function: str = "execute_flow",
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
    # Normalize language
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
    
    # Format template with provided values
    # Handle Go method naming (capitalize first letter for exported)
    go_method = method_name[0].upper() + method_name[1:] if method_name else "Execute"
    go_flow = flow_function[0].upper() + flow_function[1:] if flow_function else "ExecuteFlow"
    
    return template.format(
        provider_title=provider_title,
        task_title=task_title,
        client_class=client_class,
        method_name=go_method if lang_key == "go" else method_name,
        flow_function=go_flow if lang_key == "go" else flow_function,
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

```{code_fence_lang}
{skeleton_code}
```

## CONSTRAINTS (MUST FOLLOW)
1. Return ONLY valid {lang_name} code, no markdown fences or explanations
2. Keep all imports, class names, method names, and function signatures exactly as provided
3. Add comprehensive documentation using {conventions["docstring_style"]}
4. Use {type_hints} throughout
5. Include proper error handling ({error_pattern})
6. Do NOT hardcode any URLs, API keys, or test data
7. Follow {style_guide} conventions

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
    Per PRODUCTION_AUDIT_DEC12.md - Path hallucination prevention.
    """
    if not state.endpoints:
        return ""
    
    # Group paths by method for better readability
    paths_by_method: dict[str, list[str]] = {}
    for ep in state.endpoints:
        method = ep.method.upper() if ep.method else "UNKNOWN"
        if method not in paths_by_method:
            paths_by_method[method] = []
        paths_by_method[method].append(ep.path)
    
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
    
    path_num = 1
    for method in sorted(paths_by_method.keys()):
        paths = sorted(set(paths_by_method[method]))  # Deduplicate and sort
        lines.append(f"### {method} endpoints:")
        # Limit to first 25 paths per method to avoid prompt bloat
        display_paths = paths[:25]
        for path in display_paths:
            # Add quotes to make exact string clear
            lines.append(f'  [{path_num}] "{path}"')
            path_num += 1
        if len(paths) > 25:
            lines.append(f"  ... and {len(paths) - 25} more {method} endpoints")
        lines.append("")
    
    lines.extend([
        "="*60,
        "## PATH RULES (VIOLATIONS CAUSE GENERATION FAILURE):",
        "",
        "1. ✅ ONLY use paths that appear in the numbered list above",
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
        return f"""Generate an API client class in {lang_name}.
- Class name must be: {client_class}
- Main method must be: {method_name}
- The method should accept a payload (dict/object) and optional idempotency_key
- Handle HTTP errors appropriately ({error_pattern})
- Support configurable base_url via constructor
- Use {conventions['http_library']} for HTTP requests"""

    elif artifact_kind == "flow":
        return f"""Generate a workflow/flow function in {lang_name}.
- Function name must be: {flow_function}
- Accept api_key, payload, and additional options
- Validate input parameters before calling the API
- Transform and return the API response
- Handle validation errors appropriately
- Handle API errors via the client"""

    elif artifact_kind == "test":
        # Bug #31 Fix: Add explicit credential handling instructions (now language-aware)
        return f"""Generate {test_framework} test cases in {lang_name}.
- Use {test_framework} and mocking utilities
- Mock the API client to avoid real network calls
- Test both success and error scenarios
- Use meaningful test data that reflects the API
- Test input validation

CRITICAL CREDENTIAL RULES (must follow):
- NEVER hardcode API keys, tokens, or credentials in test code
- Use environment variables or test fixtures for credentials
- Use placeholder strings like "test_api_key" ONLY for mocked tests where the actual value doesn't matter
- For test data, use clearly fake values like "test_123" not realistic-looking keys"""

    return f"Generate clean, well-documented {lang_name} code."


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
) -> str:
    """
    Build a prompt for constrained code generation.
    
    V2.2 (Dynamic Capability Fix #1): Constrained generation injects FIXED paths 
    from the spec and asks the LLM to only fill in the method body logic.
    This prevents path hallucination (Bug #29, #30).
    
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
    
    Returns:
        A prompt string that constrains the LLM to only fill method bodies
    """
    # Format schemas if available
    request_schema_str = "No request body required."
    if request_schema and spec:
        request_schema_str = _format_schema(request_schema, spec)
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
    
    prompt = f"""You are a senior Python developer. Fill in ONLY the method body for the following API client.

## CONTEXT
Provider: {provider_code}
Endpoint: {endpoint_desc}
HTTP Method: {http_method.upper()}
Path: {endpoint_path}

## REQUEST SCHEMA
{request_schema_str}

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
            headers["Idempotency-Key"] = idempotency_key
        
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
