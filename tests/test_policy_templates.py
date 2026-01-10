"""
Tests for P1: Policy Wiring to Code

Tests the policy template generation and code injection for
AUTH, RETRY, RATE_LIMIT, LOGGING, and IDEMPOTENCY policies.
"""
import pytest
from typing import List, Dict, Any

from integration_coworker.codegen.policy_templates import (
    PolicyCodeSnippet,
    get_auth_template,
    get_retry_template,
    get_rate_limit_template,
    get_logging_template,
    get_idempotency_template,
    build_policy_code,
    inject_policies_into_client_code,
)


# Mark all tests in this module as not needing database
pytestmark = pytest.mark.no_db


# ---------------------------------------------------------------------------
# Test: Auth Templates
# ---------------------------------------------------------------------------

class TestAuthTemplates:
    """Tests for authentication code templates."""
    
    def test_bearer_auth_template(self):
        """Bearer token auth should generate correct code."""
        config = {"type": "bearer", "env_var": "API_TOKEN"}
        snippet = get_auth_template(config)
        
        assert isinstance(snippet, PolicyCodeSnippet)
        assert "import os" in snippet.imports
        assert "Bearer" in snippet.pre_request_code
        assert "Authorization" in snippet.pre_request_code
        assert "_auth_token" in snippet.setup_code
    
    def test_api_key_header_template(self):
        """API key in header should generate correct code."""
        config = {"type": "api_key", "location": "header", "key_name": "X-API-Key"}
        snippet = get_auth_template(config)
        
        assert "X-API-Key" in snippet.pre_request_code
        assert "headers" in snippet.pre_request_code
        assert "_api_key" in snippet.setup_code
    
    def test_api_key_query_template(self):
        """API key in query should generate correct code."""
        config = {"type": "api_key", "location": "query", "key_name": "api_key"}
        snippet = get_auth_template(config)
        
        assert "params" in snippet.pre_request_code
        assert "api_key" in snippet.pre_request_code
    
    def test_basic_auth_template(self):
        """Basic auth should generate base64 encoding code."""
        config = {"type": "basic"}
        snippet = get_auth_template(config)
        
        assert "base64" in snippet.imports[1] if len(snippet.imports) > 1 else "base64" in str(snippet.imports)
        assert "_basic_auth" in snippet.setup_code
        assert "Basic" in snippet.setup_code
    
    def test_oauth2_template(self):
        """OAuth2 should generate token refresh code."""
        config = {
            "type": "oauth2",
            "flow": "client_credentials",
            "token_url": "https://auth.example.com/token"
        }
        snippet = get_auth_template(config)
        
        assert "httpx" in str(snippet.imports)
        assert "_refresh_oauth_token" in snippet.wrapper_code
        assert "token_url" in snippet.setup_code
    
    def test_no_auth_template(self):
        """No auth should generate empty code."""
        config = {"type": "none"}
        snippet = get_auth_template(config)
        
        assert snippet.imports == []
        assert snippet.setup_code == ""
        assert snippet.pre_request_code == ""


# ---------------------------------------------------------------------------
# Test: Retry Templates
# ---------------------------------------------------------------------------

class TestRetryTemplates:
    """Tests for retry logic code templates."""
    
    def test_retry_template_config(self):
        """Retry template should use provided config."""
        config = {
            "max_attempts": 5,
            "backoff_type": "exponential",
            "initial_delay_ms": 200,
            "max_delay_ms": 10000,
            "retryable_status_codes": [429, 500, 503],
        }
        snippet = get_retry_template(config)
        
        assert "5" in snippet.setup_code  # max_retries
        assert "200" in snippet.setup_code  # initial_delay_ms
        assert "10000" in snippet.setup_code  # max_delay_ms
        assert "429" in snippet.setup_code
        assert "_with_retry" in snippet.wrapper_code
    
    def test_retry_template_exponential_backoff(self):
        """Exponential backoff should double delay."""
        config = {"backoff_type": "exponential"}
        snippet = get_retry_template(config)
        
        assert "delay_ms *= 2" in snippet.wrapper_code
    
    def test_retry_template_imports(self):
        """Retry template should include time and random imports."""
        config = {}
        snippet = get_retry_template(config)
        
        assert "import time" in snippet.imports
        assert "import random" in snippet.imports


# ---------------------------------------------------------------------------
# Test: Rate Limit Templates
# ---------------------------------------------------------------------------

class TestRateLimitTemplates:
    """Tests for rate limiting code templates."""
    
    def test_rate_limit_template_config(self):
        """Rate limit template should use provided config."""
        config = {
            "requests_per_second": 5,
            "burst_size": 10,
        }
        snippet = get_rate_limit_template(config)
        
        assert "5" in snippet.setup_code  # rate_limit
        assert "10" in snippet.setup_code  # burst_size
        assert "_acquire_rate_limit_token" in snippet.wrapper_code
    
    def test_rate_limit_template_threading(self):
        """Rate limit should use threading lock."""
        config = {}
        snippet = get_rate_limit_template(config)
        
        assert "import threading" in snippet.imports
        assert "_rate_limit_lock" in snippet.setup_code
        assert "with self._rate_limit_lock" in snippet.wrapper_code


# ---------------------------------------------------------------------------
# Test: Logging Templates
# ---------------------------------------------------------------------------

class TestLoggingTemplates:
    """Tests for logging code templates."""
    
    def test_logging_template_basic(self):
        """Logging template should generate logging code."""
        config = {
            "log_request": True,
            "log_response": True,
        }
        snippet = get_logging_template(config)
        
        assert "import logging" in snippet.imports
        assert "_logger" in snippet.setup_code
        assert "_log_request" in snippet.wrapper_code
        assert "_log_response" in snippet.wrapper_code
    
    def test_logging_template_redaction(self):
        """Logging template should handle field redaction."""
        config = {
            "redact_fields": ["Authorization", "api_key"],
        }
        snippet = get_logging_template(config)
        
        assert "_redact_sensitive" in snippet.wrapper_code
        assert "Authorization" in snippet.setup_code
        assert "api_key" in snippet.setup_code


# ---------------------------------------------------------------------------
# Test: Idempotency Templates
# ---------------------------------------------------------------------------

class TestIdempotencyTemplates:
    """Tests for idempotency key code templates."""
    
    def test_idempotency_uuid_generator(self):
        """UUID generator should use uuid4."""
        config = {
            "header_name": "Idempotency-Key",
            "key_generator": "uuid4",
        }
        snippet = get_idempotency_template(config)
        
        assert "import uuid" in snippet.imports
        assert "uuid.uuid4()" in snippet.pre_request_code
        assert "Idempotency-Key" in snippet.setup_code
    
    def test_idempotency_hash_generator(self):
        """Hash generator should use hashlib."""
        config = {
            "header_name": "X-Request-Id",
            "key_generator": "content_hash",
        }
        snippet = get_idempotency_template(config)
        
        assert "import hashlib" in snippet.imports
        assert "sha256" in snippet.pre_request_code


# ---------------------------------------------------------------------------
# Test: build_policy_code
# ---------------------------------------------------------------------------

class TestBuildPolicyCode:
    """Tests for combining multiple policy templates."""
    
    def test_combines_multiple_policies(self):
        """Should combine code from multiple policies."""
        policies = [
            {"policy_type": "AUTH", "config": {"type": "bearer"}},
            {"policy_type": "RETRY", "config": {"max_attempts": 3}},
        ]
        
        result = build_policy_code(policies)
        
        assert "import os" in result["imports"]
        assert "import time" in result["imports"]
        assert "_auth_token" in result["setup_code"]
        assert "_max_retries" in result["setup_code"]
    
    def test_deduplicates_imports(self):
        """Should not duplicate import statements."""
        policies = [
            {"policy_type": "RETRY", "config": {}},
            {"policy_type": "RATE_LIMIT", "config": {}},
        ]
        
        result = build_policy_code(policies)
        
        # Count occurrences of "import time"
        imports = result["imports"]
        assert imports.count("import time") == 1
    
    def test_filters_by_policy_type(self):
        """Should filter to specific policy types."""
        policies = [
            {"policy_type": "AUTH", "config": {"type": "bearer"}},
            {"policy_type": "RETRY", "config": {}},
            {"policy_type": "LOGGING", "config": {}},
        ]
        
        result = build_policy_code(policies, policy_types=["AUTH"])
        
        assert "_auth_token" in result["setup_code"]
        assert "_max_retries" not in result["setup_code"]
        assert "_logger" not in result["setup_code"]
    
    def test_handles_enum_policy_types(self):
        """Should handle enum-style policy types."""
        from enum import Enum
        
        class MockPolicyType(Enum):
            AUTH = "AUTH"
        
        policies = [
            {"policy_type": MockPolicyType.AUTH, "config": {"type": "bearer"}},
        ]
        
        result = build_policy_code(policies)
        
        assert "_auth_token" in result["setup_code"]


# ---------------------------------------------------------------------------
# Test: inject_policies_into_client_code
# ---------------------------------------------------------------------------

class TestInjectPoliciesIntoClientCode:
    """Tests for injecting policies into client code."""
    
    def test_injects_imports(self):
        """Should add policy imports to client code."""
        client_code = '''
import httpx

class StripeClient:
    def __init__(self):
        self.base_url = "https://api.stripe.com"
    
    def create_session(self):
        pass
'''
        policies = [{"policy_type": "AUTH", "config": {"type": "bearer"}}]
        
        result = inject_policies_into_client_code(client_code, policies)
        
        assert "import os" in result
        assert "# Policy imports" in result
    
    def test_injects_setup_code(self):
        """Should add setup code in __init__."""
        client_code = '''
import httpx

class StripeClient:
    def __init__(self):
        self.base_url = "https://api.stripe.com"
    
    def create_session(self):
        pass
'''
        policies = [{"policy_type": "RETRY", "config": {"max_attempts": 3}}]
        
        result = inject_policies_into_client_code(client_code, policies)
        
        assert "# Policy initialization" in result
        assert "_max_retries" in result
    
    def test_injects_wrapper_methods(self):
        """Should add helper methods at end of class."""
        client_code = '''
import httpx

class StripeClient:
    def __init__(self):
        self.base_url = "https://api.stripe.com"
    
    def create_session(self):
        pass
'''
        policies = [{"policy_type": "RATE_LIMIT", "config": {}}]
        
        result = inject_policies_into_client_code(client_code, policies)
        
        assert "# Policy helper methods" in result
        assert "_acquire_rate_limit_token" in result
    
    def test_preserves_original_code(self):
        """Should preserve original client code structure."""
        client_code = '''
import httpx

class StripeClient:
    def __init__(self):
        self.base_url = "https://api.stripe.com"
    
    def create_session(self):
        return self.base_url
'''
        policies = [{"policy_type": "AUTH", "config": {"type": "none"}}]
        
        result = inject_policies_into_client_code(client_code, policies)
        
        assert "class StripeClient:" in result
        assert "def create_session(self):" in result
        assert "return self.base_url" in result
    
    def test_handles_empty_policies(self):
        """Should handle empty policy list gracefully."""
        client_code = '''
class SimpleClient:
    def __init__(self):
        pass
'''
        policies: List[Dict[str, Any]] = []
        
        result = inject_policies_into_client_code(client_code, policies)
        
        # Should return original code unchanged (no policy markers added)
        assert "class SimpleClient:" in result
