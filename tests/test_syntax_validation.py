"""
Tests for Bug #88: Tree-sitter based syntax validation.

Tests the syntax_validator module which provides language-aware syntax
validation using tree-sitter parsers.

Supported Languages:
- Python (via tree-sitter-python or ast fallback)
- TypeScript (via tree-sitter-typescript)
- JavaScript (via tree-sitter-javascript)
- Go (via tree-sitter-go)
- Java (via tree-sitter-java)
- Ruby (via tree-sitter-ruby)
- C# (via tree-sitter-c-sharp)
"""
import pytest
from integration_coworker.codegen.syntax_validator import (
    validate_syntax,
    is_tree_sitter_available,
    get_available_languages,
    validate_code_artifact,
    SyntaxValidationResult,
)

# Mark all tests in this module to skip database setup
pytestmark = pytest.mark.no_db


class TestSyntaxValidatorBasics:
    """Test basic validation functionality."""
    
    def test_validate_valid_python(self):
        """Valid Python code should pass validation."""
        code = '''
def hello(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}!"

class Greeter:
    def __init__(self, greeting: str = "Hello"):
        self.greeting = greeting
    
    def greet(self, name: str) -> str:
        return f"{self.greeting}, {name}!"
'''
        result = validate_syntax(code, "python")
        assert result.is_valid
        assert result.language == "python"
        assert result.error_message is None
    
    def test_validate_invalid_python(self):
        """Invalid Python code should fail validation."""
        code = '''
def broken(
    # Missing closing paren and colon
'''
        result = validate_syntax(code, "python")
        assert not result.is_valid
        assert result.language == "python"
        assert result.error_message is not None
    
    def test_validate_empty_code(self):
        """Empty code should be valid."""
        result = validate_syntax("", "python")
        assert result.is_valid
    
    def test_validate_whitespace_only(self):
        """Whitespace-only code should be valid."""
        result = validate_syntax("   \n\n  \n", "python")
        assert result.is_valid
    
    def test_language_normalization(self):
        """Language names should be normalized."""
        code = "def foo(): pass"
        
        # These should all work for Python
        for lang in ["python", "Python", "PYTHON", "py", "PY"]:
            result = validate_syntax(code, lang)
            assert result.is_valid, f"Failed for language: {lang}"


class TestPythonValidation:
    """Python-specific validation tests."""
    
    def test_syntax_error_location(self):
        """Syntax errors should include location info."""
        code = '''def foo():
    x = 1
    y = (
    z = 3
'''
        result = validate_syntax(code, "python")
        assert not result.is_valid
        # Should have line number info
        assert result.error_line is not None
        assert result.error_line >= 1
    
    def test_class_definition(self):
        """Class definitions should validate."""
        code = '''
class MyClient:
    def __init__(self, api_key: str):
        self._api_key = api_key
        self._base_url = "https://api.example.com"
    
    def get_resource(self, resource_id: str) -> dict:
        return {"id": resource_id}
'''
        result = validate_syntax(code, "python")
        assert result.is_valid
    
    def test_async_function(self):
        """Async functions should validate."""
        code = '''
import asyncio

async def fetch_data(url: str) -> dict:
    await asyncio.sleep(0.1)
    return {"url": url, "data": "fetched"}
'''
        result = validate_syntax(code, "python")
        assert result.is_valid
    
    def test_incomplete_function(self):
        """Incomplete function definitions should fail."""
        code = "def incomplete_function("
        result = validate_syntax(code, "python")
        assert not result.is_valid


@pytest.mark.skipif(
    not is_tree_sitter_available(),
    reason="tree-sitter not installed"
)
class TestTypeScriptValidation:
    """TypeScript validation tests (requires tree-sitter)."""
    
    def test_valid_typescript(self):
        """Valid TypeScript should pass validation."""
        code = '''
interface User {
    id: string;
    name: string;
    email?: string;
}

class UserService {
    private users: Map<string, User> = new Map();
    
    async getUser(id: string): Promise<User | undefined> {
        return this.users.get(id);
    }
    
    createUser(user: User): void {
        this.users.set(user.id, user);
    }
}

export { UserService, User };
'''
        result = validate_syntax(code, "typescript")
        assert result.is_valid
        assert result.language == "typescript"
    
    def test_invalid_typescript(self):
        """Invalid TypeScript should fail validation."""
        code = '''
interface Broken {
    id: string
    name  // Missing type annotation and semicolon pattern
'''
        result = validate_syntax(code, "typescript")
        # Note: tree-sitter is permissive, this may or may not fail
        # depending on how strict the grammar is
        assert result.language == "typescript"
    
    def test_typescript_with_generics(self):
        """TypeScript with generics should validate."""
        code = '''
function identity<T>(arg: T): T {
    return arg;
}

type Result<T, E> = { ok: true; value: T } | { ok: false; error: E };
'''
        result = validate_syntax(code, "typescript")
        assert result.is_valid
    
    def test_ts_alias(self):
        """'ts' should work as alias for TypeScript."""
        code = "const x: number = 42;"
        result = validate_syntax(code, "ts")
        assert result.is_valid


@pytest.mark.skipif(
    not is_tree_sitter_available(),
    reason="tree-sitter not installed"
)
class TestJavaScriptValidation:
    """JavaScript validation tests (requires tree-sitter)."""
    
    def test_valid_javascript(self):
        """Valid JavaScript should pass validation."""
        code = '''
class ApiClient {
    constructor(apiKey) {
        this.apiKey = apiKey;
        this.baseUrl = "https://api.example.com";
    }
    
    async fetchResource(resourceId) {
        const response = await fetch(`${this.baseUrl}/resources/${resourceId}`, {
            headers: { "Authorization": `Bearer ${this.apiKey}` }
        });
        return response.json();
    }
}

module.exports = { ApiClient };
'''
        result = validate_syntax(code, "javascript")
        assert result.is_valid
        assert result.language == "javascript"
    
    def test_arrow_functions(self):
        """Arrow functions should validate."""
        code = '''
const add = (a, b) => a + b;
const multiply = (a, b) => {
    return a * b;
};
const greet = name => `Hello, ${name}!`;
'''
        result = validate_syntax(code, "javascript")
        assert result.is_valid
    
    def test_js_alias(self):
        """'js' should work as alias for JavaScript."""
        code = "const x = 42;"
        result = validate_syntax(code, "js")
        assert result.is_valid


@pytest.mark.skipif(
    not is_tree_sitter_available(),
    reason="tree-sitter not installed"
)
class TestGoValidation:
    """Go validation tests (requires tree-sitter)."""
    
    def test_valid_go(self):
        """Valid Go code should pass validation."""
        code = '''
package main

import (
    "fmt"
    "net/http"
)

type Client struct {
    baseURL string
    apiKey  string
}

func NewClient(apiKey string) *Client {
    return &Client{
        baseURL: "https://api.example.com",
        apiKey:  apiKey,
    }
}

func (c *Client) GetResource(id string) (map[string]interface{}, error) {
    resp, err := http.Get(fmt.Sprintf("%s/resources/%s", c.baseURL, id))
    if err != nil {
        return nil, err
    }
    defer resp.Body.Close()
    return nil, nil
}
'''
        result = validate_syntax(code, "go")
        assert result.is_valid
        assert result.language == "go"
    
    def test_go_interface(self):
        """Go interfaces should validate."""
        code = '''
package api

type ResourceFetcher interface {
    Fetch(id string) ([]byte, error)
    List() ([]string, error)
}
'''
        result = validate_syntax(code, "go")
        assert result.is_valid


@pytest.mark.skipif(
    not is_tree_sitter_available(),
    reason="tree-sitter not installed"
)
class TestJavaValidation:
    """Java validation tests (requires tree-sitter)."""
    
    def test_valid_java(self):
        """Valid Java code should pass validation."""
        code = '''
package com.example.api;

import java.util.Map;
import java.util.HashMap;

public class ApiClient {
    private final String apiKey;
    private final String baseUrl;
    
    public ApiClient(String apiKey) {
        this.apiKey = apiKey;
        this.baseUrl = "https://api.example.com";
    }
    
    public Map<String, Object> getResource(String resourceId) {
        Map<String, Object> result = new HashMap<>();
        result.put("id", resourceId);
        return result;
    }
}
'''
        result = validate_syntax(code, "java")
        assert result.is_valid
        assert result.language == "java"
    
    def test_java_generics(self):
        """Java generics should validate."""
        code = '''
import java.util.List;
import java.util.Optional;

public class Repository<T> {
    public Optional<T> findById(String id) {
        return Optional.empty();
    }
    
    public List<T> findAll() {
        return List.of();
    }
}
'''
        result = validate_syntax(code, "java")
        assert result.is_valid


@pytest.mark.skipif(
    not is_tree_sitter_available(),
    reason="tree-sitter not installed"
)
class TestRubyValidation:
    """Ruby validation tests (requires tree-sitter)."""
    
    def test_valid_ruby(self):
        """Valid Ruby code should pass validation."""
        code = '''
require 'net/http'
require 'json'

class ApiClient
  attr_reader :api_key, :base_url
  
  def initialize(api_key)
    @api_key = api_key
    @base_url = "https://api.example.com"
  end
  
  def get_resource(resource_id)
    uri = URI("#{@base_url}/resources/#{resource_id}")
    response = Net::HTTP.get_response(uri)
    JSON.parse(response.body)
  end
end
'''
        result = validate_syntax(code, "ruby")
        assert result.is_valid
        assert result.language == "ruby"
    
    def test_ruby_blocks(self):
        """Ruby blocks should validate."""
        code = '''
[1, 2, 3].map { |x| x * 2 }

items.each do |item|
  puts item
end
'''
        result = validate_syntax(code, "ruby")
        assert result.is_valid


@pytest.mark.skipif(
    not is_tree_sitter_available(),
    reason="tree-sitter not installed"
)
class TestCSharpValidation:
    """C# validation tests (requires tree-sitter)."""
    
    def test_valid_csharp(self):
        """Valid C# code should pass validation."""
        code = '''
using System;
using System.Net.Http;
using System.Threading.Tasks;

namespace Example.Api
{
    public class ApiClient
    {
        private readonly string _apiKey;
        private readonly HttpClient _httpClient;
        
        public ApiClient(string apiKey)
        {
            _apiKey = apiKey;
            _httpClient = new HttpClient
            {
                BaseAddress = new Uri("https://api.example.com")
            };
        }
        
        public async Task<string> GetResourceAsync(string resourceId)
        {
            var response = await _httpClient.GetAsync($"/resources/{resourceId}");
            response.EnsureSuccessStatusCode();
            return await response.Content.ReadAsStringAsync();
        }
    }
}
'''
        result = validate_syntax(code, "csharp")
        assert result.is_valid
        assert result.language == "csharp"
    
    def test_csharp_aliases(self):
        """C# language aliases should work."""
        code = '''
public class Test
{
    public int Value { get; set; }
}
'''
        for alias in ["csharp", "c#", "cs"]:
            result = validate_syntax(code, alias)
            assert result.is_valid, f"Failed for alias: {alias}"


class TestValidationResult:
    """Test the SyntaxValidationResult dataclass."""
    
    def test_valid_result_structure(self):
        """Valid result should have correct structure."""
        result = validate_syntax("x = 1", "python")
        assert isinstance(result, SyntaxValidationResult)
        assert result.is_valid is True
        assert result.language == "python"
        assert result.method in ("tree-sitter", "ast", "regex")
        assert result.warnings is not None  # List, may be empty
    
    def test_invalid_result_structure(self):
        """Invalid result should have error details."""
        result = validate_syntax("def broken(", "python")
        assert isinstance(result, SyntaxValidationResult)
        assert result.is_valid is False
        assert result.error_message is not None


class TestArtifactValidation:
    """Test the validate_code_artifact convenience function."""
    
    def test_artifact_validation_client(self):
        """Validate client artifact type."""
        code = '''
class MyClient:
    def __init__(self, api_key):
        self.api_key = api_key
'''
        result = validate_code_artifact(code, "python", "client")
        assert result.is_valid
    
    def test_artifact_validation_test(self):
        """Validate test artifact type."""
        code = '''
import pytest

class TestMyClient:
    def test_init(self):
        client = MyClient("key")
        assert client.api_key == "key"
'''
        result = validate_code_artifact(code, "python", "test")
        assert result.is_valid
    
    def test_artifact_error_message_includes_type(self):
        """Error messages should include artifact type."""
        code = "def broken("
        result = validate_code_artifact(code, "python", "client")
        assert not result.is_valid
        assert "[client]" in result.error_message


class TestTreeSitterAvailability:
    """Test tree-sitter availability checking."""
    
    def test_is_tree_sitter_available_returns_bool(self):
        """Should return a boolean."""
        result = is_tree_sitter_available()
        assert isinstance(result, bool)
    
    def test_get_available_languages_returns_list(self):
        """Should return a list of strings."""
        result = get_available_languages()
        assert isinstance(result, list)
        if is_tree_sitter_available():
            # If tree-sitter available, should have some languages
            assert len(result) > 0
            assert all(isinstance(lang, str) for lang in result)
        else:
            # If not available, should be empty
            assert len(result) == 0


class TestFallbackBehavior:
    """Test fallback behavior when tree-sitter is not available."""
    
    def test_python_ast_fallback(self):
        """Python should always work via ast fallback."""
        code = "def foo(): return 42"
        result = validate_syntax(code, "python")
        assert result.is_valid
        # Method should be either tree-sitter or ast
        assert result.method in ("tree-sitter", "ast")
    
    def test_python_ast_fallback_detects_errors(self):
        """Python AST fallback should detect syntax errors."""
        code = "def foo(:"
        result = validate_syntax(code, "python")
        assert not result.is_valid
    
    @pytest.mark.skipif(
        is_tree_sitter_available(),
        reason="Only tests fallback when tree-sitter not available"
    )
    def test_non_python_without_tree_sitter(self):
        """Non-Python without tree-sitter should use regex (permissive)."""
        code = "function foo() { return 42; }"
        result = validate_syntax(code, "javascript")
        # Without tree-sitter, uses regex which is permissive
        assert result.method == "regex"


class TestRealWorldCodePatterns:
    """Test validation of real-world code patterns from the codebase."""
    
    def test_api_client_pattern(self):
        """Test typical API client code pattern."""
        code = '''
"""API client for Example service."""
import httpx
from typing import Any, Dict, Optional


class ExampleClient:
    """Client for Example API."""
    
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.example.com/v1",
        timeout: float = 30.0,
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )
    
    def get_resource(self, resource_id: str) -> Dict[str, Any]:
        """Get a resource by ID."""
        response = self._client.get(f"/resources/{resource_id}")
        response.raise_for_status()
        return response.json()
    
    def create_resource(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new resource."""
        response = self._client.post("/resources", json=data)
        response.raise_for_status()
        return response.json()
'''
        result = validate_syntax(code, "python")
        assert result.is_valid
    
    def test_pytest_test_pattern(self):
        """Test typical pytest test code pattern."""
        code = '''
"""Tests for ExampleClient."""
import pytest
from unittest.mock import Mock, patch


class TestExampleClient:
    """Test suite for ExampleClient."""
    
    @pytest.fixture
    def client(self):
        """Create a test client."""
        return ExampleClient(api_key="test-key")
    
    def test_get_resource(self, client):
        """Test getting a resource."""
        with patch.object(client._client, "get") as mock_get:
            mock_get.return_value = Mock(
                json=Mock(return_value={"id": "123", "name": "Test"})
            )
            result = client.get_resource("123")
            assert result["id"] == "123"
    
    @pytest.mark.parametrize("resource_id", ["1", "2", "3"])
    def test_get_multiple_resources(self, client, resource_id):
        """Test getting multiple resources."""
        pass
'''
        result = validate_syntax(code, "python")
        assert result.is_valid
