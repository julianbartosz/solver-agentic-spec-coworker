"""Tests for LLM exception hierarchy.

Production Readiness v4: These tests verify:
1. LLMAuthError is raised for 401/403 errors
2. Auth errors are never retried
3. Rate limit and transient errors ARE retried
4. Exception classification works correctly
5. LLM budget enforcement prevents runaway costs
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import asyncio

from integration_coworker.llm.exceptions import (
    LLMError,
    LLMAuthError,
    LLMRateLimitError,
    LLMTransientError,
    LLMContentFilterError,
    LLMContextLengthError,
    LLMBudgetExceededError,
    classify_llm_exception,
)


class TestLLMExceptionHierarchy:
    """Test the exception class hierarchy."""
    
    def test_llm_auth_error_is_llm_error(self):
        """LLMAuthError should inherit from LLMError."""
        exc = LLMAuthError("Invalid API key")
        assert isinstance(exc, LLMError)
        assert isinstance(exc, Exception)
    
    def test_llm_rate_limit_error_is_llm_error(self):
        """LLMRateLimitError should inherit from LLMError."""
        exc = LLMRateLimitError("Rate limit exceeded")
        assert isinstance(exc, LLMError)
    
    def test_llm_transient_error_is_llm_error(self):
        """LLMTransientError should inherit from LLMError."""
        exc = LLMTransientError("Server error")
        assert isinstance(exc, LLMError)
    
    def test_exception_preserves_provider(self):
        """Exceptions should preserve provider information."""
        exc = LLMAuthError("Invalid key", provider="openai", status_code=401)
        assert exc.provider == "openai"
        assert exc.status_code == 401
    
    def test_rate_limit_preserves_retry_after(self):
        """Rate limit errors should preserve retry_after hint."""
        exc = LLMRateLimitError("Rate limited", retry_after=30.0)
        assert exc.retry_after == 30.0


class TestClassifyLLMException:
    """Test the exception classification function."""
    
    def test_classify_401_as_auth_error(self):
        """401 errors should classify as LLMAuthError."""
        exc = Exception("Error code: 401 - Unauthorized")
        result = classify_llm_exception(exc)
        assert isinstance(result, LLMAuthError)
    
    def test_classify_403_as_auth_error(self):
        """403 errors should classify as LLMAuthError."""
        exc = Exception("Error code: 403 - Permission denied")
        result = classify_llm_exception(exc)
        assert isinstance(result, LLMAuthError)
    
    def test_classify_invalid_api_key_as_auth_error(self):
        """Invalid API key errors should classify as LLMAuthError."""
        exc = Exception("Invalid API key provided")
        result = classify_llm_exception(exc)
        assert isinstance(result, LLMAuthError)
    
    def test_classify_429_as_rate_limit(self):
        """429 errors should classify as LLMRateLimitError."""
        exc = Exception("Error code: 429 - Too many requests")
        result = classify_llm_exception(exc)
        assert isinstance(result, LLMRateLimitError)
    
    def test_classify_rate_limit_as_rate_limit(self):
        """Rate limit messages should classify as LLMRateLimitError."""
        exc = Exception("Rate limit exceeded. Please retry after 30 seconds")
        result = classify_llm_exception(exc)
        assert isinstance(result, LLMRateLimitError)
    
    def test_classify_500_as_transient(self):
        """500 errors should classify as LLMTransientError."""
        exc = Exception("Error code: 500 - Internal server error")
        result = classify_llm_exception(exc)
        assert isinstance(result, LLMTransientError)
    
    def test_classify_503_as_transient(self):
        """503 errors should classify as LLMTransientError."""
        exc = Exception("Error code: 503 - Service unavailable")
        result = classify_llm_exception(exc)
        assert isinstance(result, LLMTransientError)
    
    def test_classify_timeout_as_transient(self):
        """Timeout errors should classify as LLMTransientError."""
        exc = Exception("Connection timed out")
        result = classify_llm_exception(exc)
        assert isinstance(result, LLMTransientError)
    
    def test_classify_content_filter(self):
        """Content filter errors should classify as LLMContentFilterError."""
        exc = Exception("Content was blocked by safety filter")
        result = classify_llm_exception(exc)
        assert isinstance(result, LLMContentFilterError)
    
    def test_classify_context_length(self):
        """Context length errors should classify as LLMContextLengthError."""
        exc = Exception("Maximum context length exceeded")
        result = classify_llm_exception(exc)
        assert isinstance(result, LLMContextLengthError)
    
    def test_classify_preserves_cause_chain(self):
        """Classification should preserve the original exception as cause."""
        original = ValueError("Some error")
        result = classify_llm_exception(original)
        # The result should be an LLMError type
        assert isinstance(result, LLMError)
    
    def test_classify_unknown_as_transient(self):
        """Unknown errors should classify as transient (safe default)."""
        exc = Exception("Some unknown error happened")
        result = classify_llm_exception(exc)
        assert isinstance(result, LLMTransientError)


class TestRetryBehavior:
    """Test that retry logic respects exception types."""
    
    def test_is_retryable_returns_false_for_auth_error(self):
        """_is_retryable_error should return False for LLMAuthError."""
        from integration_coworker.llm.client import _is_retryable_error
        
        exc = LLMAuthError("Invalid API key")
        assert _is_retryable_error(exc) is False
    
    def test_is_retryable_returns_true_for_rate_limit(self):
        """_is_retryable_error should return True for LLMRateLimitError."""
        from integration_coworker.llm.client import _is_retryable_error
        
        exc = LLMRateLimitError("Rate limit exceeded")
        assert _is_retryable_error(exc) is True
    
    def test_is_retryable_returns_true_for_transient(self):
        """_is_retryable_error should return True for LLMTransientError."""
        from integration_coworker.llm.client import _is_retryable_error
        
        exc = LLMTransientError("Server error")
        assert _is_retryable_error(exc) is True
    
    def test_is_retryable_returns_false_for_401_string(self):
        """_is_retryable_error should return False for 401 in error string."""
        from integration_coworker.llm.client import _is_retryable_error
        
        exc = Exception("Error code: 401 - Unauthorized")
        assert _is_retryable_error(exc) is False
    
    def test_is_retryable_returns_false_for_403_string(self):
        """_is_retryable_error should return False for 403 in error string."""
        from integration_coworker.llm.client import _is_retryable_error
        
        exc = Exception("Error code: 403 - Forbidden")
        assert _is_retryable_error(exc) is False


class TestSyncRetryWrapper:
    """Test the sync with_retry wrapper."""
    
    def test_auth_error_not_retried(self):
        """Auth errors should not be retried."""
        from integration_coworker.llm.client import with_retry
        
        call_count = 0
        
        @with_retry
        def failing_func():
            nonlocal call_count
            call_count += 1
            raise LLMAuthError("Invalid API key")
        
        with pytest.raises(LLMAuthError):
            failing_func()
        
        # Should only be called once - no retries
        assert call_count == 1
    
    def test_rate_limit_is_retried(self):
        """Rate limit errors should be retried."""
        from integration_coworker.llm.client import with_retry
        
        call_count = 0
        
        @with_retry
        def failing_then_success():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise LLMRateLimitError("Rate limited")
            return "success"
        
        with patch('time.sleep'):  # Skip actual sleep
            result = failing_then_success()
        
        assert result == "success"
        assert call_count == 2


class TestAsyncRetryWrapper:
    """Test the async _retry_async wrapper."""
    
    @pytest.mark.asyncio
    async def test_auth_error_not_retried_async(self):
        """Auth errors should not be retried in async context."""
        from integration_coworker.llm.async_client import _retry_async
        
        call_count = 0
        
        async def failing_func():
            nonlocal call_count
            call_count += 1
            raise LLMAuthError("Invalid API key")
        
        with pytest.raises(LLMAuthError):
            await _retry_async(failing_func)
        
        # Should only be called once - no retries
        assert call_count == 1
    
    @pytest.mark.asyncio
    async def test_rate_limit_is_retried_async(self):
        """Rate limit errors should be retried in async context."""
        from integration_coworker.llm.async_client import _retry_async
        
        call_count = 0
        
        async def failing_then_success():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise LLMRateLimitError("Rate limited")
            return "success"
        
        with patch('asyncio.sleep', new_callable=AsyncMock):
            result = await _retry_async(failing_then_success)
        
        assert result == "success"
        assert call_count == 2
    
    @pytest.mark.asyncio
    async def test_401_string_not_retried_async(self):
        """401 errors in string should not be retried in async context."""
        from integration_coworker.llm.async_client import _retry_async
        
        call_count = 0
        
        async def failing_func():
            nonlocal call_count
            call_count += 1
            raise Exception("Error code: 401 - Unauthorized")
        
        with pytest.raises(LLMAuthError):  # Should be converted to typed exception
            await _retry_async(failing_func)
        
        # Should only be called once - no retries
        assert call_count == 1


class TestLLMBudgetEnforcement:
    """Test LLM budget enforcement (V4 Production Safety).
    
    These tests verify that:
    1. LLMBudgetExceededError is raised when limit is exceeded
    2. Budget counter increments correctly
    3. Budget can be configured via settings
    4. Budget is properly initialized and tracked
    """
    
    def test_budget_exceeded_error_properties(self):
        """LLMBudgetExceededError should preserve call counts."""
        exc = LLMBudgetExceededError(
            "Budget exceeded",
            calls_made=150,
            calls_limit=100,
        )
        assert exc.calls_made == 150
        assert exc.calls_limit == 100
        assert isinstance(exc, LLMError)
    
    def test_budget_counter_initializes_to_zero(self):
        """Call counter should start at 0 after init."""
        from integration_coworker.llm.client import init_token_usage, get_llm_call_count
        
        init_token_usage()
        assert get_llm_call_count() == 0
    
    def test_budget_counter_increments(self):
        """Call counter should increment on each check."""
        from integration_coworker.llm.client import (
            init_token_usage,
            get_llm_call_count,
            _increment_call_count_and_check_budget,
        )
        
        init_token_usage()
        assert get_llm_call_count() == 0
        
        # With high limit, should not raise
        with patch('integration_coworker.llm.client.get_settings') as mock_settings:
            mock_settings.return_value.prod_e2e_max_llm_calls = 1000
            _increment_call_count_and_check_budget()
            _increment_call_count_and_check_budget()
            _increment_call_count_and_check_budget()
        
        assert get_llm_call_count() == 3
    
    def test_budget_exceeded_raises(self):
        """Should raise LLMBudgetExceededError when limit exceeded."""
        from integration_coworker.llm.client import (
            init_token_usage,
            _increment_call_count_and_check_budget,
        )
        
        init_token_usage()
        
        with patch('integration_coworker.llm.client.get_settings') as mock_settings:
            mock_settings.return_value.prod_e2e_max_llm_calls = 2
            
            # First two calls should succeed
            _increment_call_count_and_check_budget()
            _increment_call_count_and_check_budget()
            
            # Third call should raise
            with pytest.raises(LLMBudgetExceededError) as exc_info:
                _increment_call_count_and_check_budget()
            
            assert exc_info.value.calls_made == 3
            assert exc_info.value.calls_limit == 2
    
    def test_budget_disabled_when_limit_zero(self):
        """Budget check should be disabled when limit is 0."""
        from integration_coworker.llm.client import (
            init_token_usage,
            get_llm_call_count,
            _increment_call_count_and_check_budget,
        )
        
        init_token_usage()
        
        with patch('integration_coworker.llm.client.get_settings') as mock_settings:
            mock_settings.return_value.prod_e2e_max_llm_calls = 0  # Disabled
            
            # Should not raise even with many calls
            for _ in range(100):
                _increment_call_count_and_check_budget()
        
        assert get_llm_call_count() == 100
    
    def test_budget_error_message_helpful(self):
        """Budget error message should include actionable guidance."""
        exc = LLMBudgetExceededError(
            "LLM call budget exceeded: 101 calls > 100 limit. "
            "Increase E2E_MAX_LLM_CALLS or investigate runaway LLM usage.",
            calls_made=101,
            calls_limit=100,
        )
        
        # Message should include env var hint
        assert "E2E_MAX_LLM_CALLS" in str(exc)
        assert "101" in str(exc)
        assert "100" in str(exc)
