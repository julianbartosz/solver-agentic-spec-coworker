"""Tests for runtime error classification and policy enforcement.

Production Readiness v4 - P0-5:
Tests verify that error classification and policy work correctly:
1. Auth errors are classified as AUTH and fail-fast
2. Rate limits are classified as RATE_LIMIT and retry
3. Transient errors are classified as TRANSIENT and retry
4. Unknown errors default to FAIL_FAST (fail-closed for production safety)
"""

import pytest

from integration_coworker.graph.runtime import (
    ErrorClass,
    ErrorPolicy,
    ERROR_POLICIES,
    classify_error,
    get_error_policy,
    should_fail_fast,
)
from integration_coworker.llm.exceptions import (
    LLMAuthError,
    LLMRateLimitError,
    LLMTransientError,
    LLMContentFilterError,
    LLMContextLengthError,
)


class TestErrorClassification:
    """Test error classification from typed and untyped exceptions."""
    
    def test_classify_typed_auth_error(self):
        """LLMAuthError should classify as AUTH."""
        exc = LLMAuthError("Invalid API key")
        assert classify_error(exc) == ErrorClass.AUTH
    
    def test_classify_typed_rate_limit_error(self):
        """LLMRateLimitError should classify as RATE_LIMIT."""
        exc = LLMRateLimitError("Too many requests")
        assert classify_error(exc) == ErrorClass.RATE_LIMIT
    
    def test_classify_typed_transient_error(self):
        """LLMTransientError should classify as TRANSIENT."""
        exc = LLMTransientError("Server error")
        assert classify_error(exc) == ErrorClass.TRANSIENT
    
    def test_classify_typed_content_filter_error(self):
        """LLMContentFilterError should classify as CONTENT_FILTER."""
        exc = LLMContentFilterError("Content blocked")
        assert classify_error(exc) == ErrorClass.CONTENT_FILTER
    
    def test_classify_typed_context_length_error(self):
        """LLMContextLengthError should classify as CONTEXT_LENGTH."""
        exc = LLMContextLengthError("Maximum context length exceeded")
        assert classify_error(exc) == ErrorClass.CONTEXT_LENGTH
    
    def test_classify_untyped_401_as_auth(self):
        """Exception with '401' in message should classify as AUTH."""
        exc = Exception("Error code: 401 - Unauthorized")
        assert classify_error(exc) == ErrorClass.AUTH
    
    def test_classify_untyped_403_as_auth(self):
        """Exception with '403' in message should classify as AUTH."""
        exc = Exception("Error code: 403 - Forbidden")
        assert classify_error(exc) == ErrorClass.AUTH
    
    def test_classify_untyped_invalid_api_key_as_auth(self):
        """Exception mentioning 'invalid api key' should classify as AUTH."""
        exc = Exception("Invalid API key provided")
        assert classify_error(exc) == ErrorClass.AUTH
    
    def test_classify_untyped_429_as_rate_limit(self):
        """Exception with '429' in message should classify as RATE_LIMIT."""
        exc = Exception("Error code: 429 - Too many requests")
        assert classify_error(exc) == ErrorClass.RATE_LIMIT
    
    def test_classify_untyped_rate_limit_text_as_rate_limit(self):
        """Exception mentioning 'rate limit' should classify as RATE_LIMIT."""
        exc = Exception("Rate limit exceeded")
        assert classify_error(exc) == ErrorClass.RATE_LIMIT
    
    def test_classify_untyped_500_as_transient(self):
        """Exception with '500' in message should classify as TRANSIENT."""
        exc = Exception("Error code: 500 - Internal server error")
        assert classify_error(exc) == ErrorClass.TRANSIENT
    
    def test_classify_untyped_timeout_as_transient(self):
        """Exception mentioning 'timeout' should classify as TRANSIENT."""
        exc = Exception("Connection timed out")
        assert classify_error(exc) == ErrorClass.TRANSIENT
    
    def test_classify_untyped_content_filter_as_content_filter(self):
        """Exception mentioning 'content filter' should classify as CONTENT_FILTER."""
        exc = Exception("Request blocked by content filter")
        assert classify_error(exc) == ErrorClass.CONTENT_FILTER
    
    def test_classify_untyped_context_length_as_context_length(self):
        """Exception mentioning 'context length' should classify as CONTEXT_LENGTH."""
        exc = Exception("Maximum context length exceeded")
        assert classify_error(exc) == ErrorClass.CONTEXT_LENGTH
    
    def test_classify_unknown_error_as_unknown(self):
        """Unknown errors should classify as UNKNOWN."""
        exc = Exception("Some random error")
        assert classify_error(exc) == ErrorClass.UNKNOWN


class TestErrorPolicies:
    """Test error policy assignments."""
    
    def test_auth_policy_is_fail_fast(self):
        """AUTH errors should have FAIL_FAST policy."""
        assert ERROR_POLICIES[ErrorClass.AUTH] == ErrorPolicy.FAIL_FAST
    
    def test_rate_limit_policy_is_retry(self):
        """RATE_LIMIT errors should have RETRY_WITH_BACKOFF policy."""
        assert ERROR_POLICIES[ErrorClass.RATE_LIMIT] == ErrorPolicy.RETRY_WITH_BACKOFF
    
    def test_transient_policy_is_retry(self):
        """TRANSIENT errors should have RETRY_WITH_BACKOFF policy."""
        assert ERROR_POLICIES[ErrorClass.TRANSIENT] == ErrorPolicy.RETRY_WITH_BACKOFF
    
    def test_content_filter_policy_is_fail_fast(self):
        """CONTENT_FILTER errors should have FAIL_FAST policy."""
        assert ERROR_POLICIES[ErrorClass.CONTENT_FILTER] == ErrorPolicy.FAIL_FAST
    
    def test_context_length_policy_is_fail_fast(self):
        """CONTEXT_LENGTH errors should have FAIL_FAST policy."""
        assert ERROR_POLICIES[ErrorClass.CONTEXT_LENGTH] == ErrorPolicy.FAIL_FAST
    
    def test_unknown_policy_is_fail_fast(self):
        """UNKNOWN errors should have FAIL_FAST policy (fail-closed for safety)."""
        assert ERROR_POLICIES[ErrorClass.UNKNOWN] == ErrorPolicy.FAIL_FAST


class TestGetErrorPolicy:
    """Test get_error_policy function."""
    
    def test_get_policy_for_auth_error(self):
        """get_error_policy should return FAIL_FAST for auth errors."""
        exc = LLMAuthError("Invalid key")
        assert get_error_policy(exc) == ErrorPolicy.FAIL_FAST
    
    def test_get_policy_for_rate_limit(self):
        """get_error_policy should return RETRY_WITH_BACKOFF for rate limits."""
        exc = LLMRateLimitError("Too many requests")
        assert get_error_policy(exc) == ErrorPolicy.RETRY_WITH_BACKOFF
    
    def test_get_policy_for_unknown(self):
        """get_error_policy should return FAIL_FAST for unknown errors (fail-closed)."""
        exc = Exception("Random error")
        assert get_error_policy(exc) == ErrorPolicy.FAIL_FAST


class TestShouldFailFast:
    """Test should_fail_fast convenience function."""
    
    def test_auth_error_should_fail_fast(self):
        """Auth errors should fail fast."""
        assert should_fail_fast(LLMAuthError("Invalid key")) is True
    
    def test_rate_limit_should_not_fail_fast(self):
        """Rate limit errors should NOT fail fast."""
        assert should_fail_fast(LLMRateLimitError("Too many requests")) is False
    
    def test_transient_should_not_fail_fast(self):
        """Transient errors should NOT fail fast."""
        assert should_fail_fast(LLMTransientError("Server error")) is False
    
    def test_content_filter_should_fail_fast(self):
        """Content filter errors should fail fast."""
        assert should_fail_fast(LLMContentFilterError("Blocked")) is True
    
    def test_context_length_should_fail_fast(self):
        """Context length errors should fail fast."""
        assert should_fail_fast(LLMContextLengthError("Too long")) is True
    
    def test_unknown_should_fail_fast(self):
        """Unknown errors SHOULD fail fast (fail-closed for production safety)."""
        assert should_fail_fast(Exception("Random")) is True
    
    def test_untyped_401_should_fail_fast(self):
        """Untyped 401 errors should fail fast."""
        assert should_fail_fast(Exception("Error 401: Unauthorized")) is True
    
    def test_untyped_503_should_not_fail_fast(self):
        """Untyped 503 errors should NOT fail fast."""
        assert should_fail_fast(Exception("Error 503: Service unavailable")) is False
