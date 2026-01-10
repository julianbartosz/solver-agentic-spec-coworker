"""
Tests for handle_error node and redaction utilities.

These tests validate production hardening requirements per OWASP Logging Cheat Sheet:
- Re-entrancy guard (idempotent)
- Secret redaction (no API keys, tokens in logs/persistence)
- Log-injection sanitization (CR/LF removal)
- Stable error summary
- Structured logging
- Performance bounds (max input length)
"""
import logging
import pytest

from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.nodes.handle_error import handle_error
from integration_coworker.security.redaction import (
    redact_secrets,
    redact_secrets_in_list,
    redact_dict_values,
    sanitize_for_logging,
    redact_and_sanitize,
    normalize_error,
    MAX_REDACTION_INPUT_LENGTH,
)


# =============================================================================
# Redaction utility tests
# =============================================================================

class TestRedactSecrets:
    """Test secret redaction patterns."""
    
    def test_redacts_openai_key(self):
        """OpenAI sk-... keys are redacted."""
        text = "Error: API call failed with key sk-abc123def456ghi789jkl012mno345pqr678"
        result = redact_secrets(text)
        assert "sk-abc123" not in result
        assert "[REDACTED:OPENAI_KEY]" in result
    
    def test_redacts_openai_project_key(self):
        """OpenAI sk-proj-... keys are redacted."""
        text = "Using key sk-proj-abc123def456ghi789jkl012mno345pqr678"
        result = redact_secrets(text)
        assert "sk-proj-abc" not in result
        assert "[REDACTED:OPENAI_KEY]" in result
    
    def test_redacts_anthropic_key(self):
        """Anthropic sk-ant-... keys are redacted."""
        text = "Anthropic key: sk-ant-abc123-def456-ghi789-jkl012"
        result = redact_secrets(text)
        assert "sk-ant-abc" not in result
        assert "[REDACTED:ANTHROPIC_KEY]" in result
    
    def test_redacts_github_token(self):
        """GitHub ghp_/gho_/etc tokens are redacted."""
        text = "GitHub token: ghp_abc123def456ghi789jkl012mno345pqr678stu"
        result = redact_secrets(text)
        assert "ghp_abc" not in result
        assert "[REDACTED:GITHUB_TOKEN]" in result
    
    def test_redacts_bearer_token(self):
        """Bearer tokens in auth headers are redacted."""
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc"
        result = redact_secrets(text)
        assert "eyJ" not in result
        # Authorization header pattern matches first, redacting entire header
        assert "Authorization: [REDACTED]" in result or "Bearer [REDACTED:TOKEN]" in result
    
    def test_redacts_api_key_query_param(self):
        """API keys in query strings are redacted."""
        text = "GET https://api.example.com/v1?api_key=secret123&other=value"
        result = redact_secrets(text)
        assert "secret123" not in result
        assert "api_key=[REDACTED]" in result
    
    def test_preserves_non_secrets(self):
        """Normal text without secrets is preserved."""
        text = "Error: Connection timeout after 30s"
        result = redact_secrets(text)
        assert result == text
    
    def test_handles_empty_string(self):
        """Empty string returns empty string."""
        assert redact_secrets("") == ""
    
    def test_handles_none(self):
        """None input returns None."""
        assert redact_secrets(None) is None


class TestRedactSecretsInList:
    """Test in-place list redaction."""
    
    def test_redacts_list_in_place(self):
        """List is modified in-place."""
        errors = [
            "API call failed: key=sk-abc123def456ghi789jkl012",
            "Network timeout",
            "Auth error: Bearer token_xyz_abc123def456",
        ]
        result = redact_secrets_in_list(errors)
        
        # Same list object returned
        assert result is errors
        
        # Items are redacted
        assert "[REDACTED:OPENAI_KEY]" in errors[0]
        assert errors[1] == "Network timeout"
        assert "[REDACTED:TOKEN]" in errors[2]
    
    def test_handles_empty_list(self):
        """Empty list returns empty list."""
        errors = []
        result = redact_secrets_in_list(errors)
        assert result == []
        assert result is errors
    
    def test_handles_mixed_types_bug6(self):
        """BUG-6: Handles mixed string and dict errors without crashing."""
        # This is the exact scenario that caused the AttributeError crash
        errors = [
            "Plain string error",
            {"node": "gen", "error": "LLM timeout", "timestamp": "2024-01-01T00:00:00Z"},
            {"error": "Another dict error"},
            "Another string error",
        ]
        result = redact_secrets_in_list(errors)
        
        # All items normalized to strings
        assert all(isinstance(e, str) for e in result)
        assert "Plain string error" in result[0]
        assert "[gen]" in result[1]
        assert "LLM timeout" in result[1]
        assert "Another dict error" in result[2]
        assert "Another string error" in result[3]
    
    def test_handles_dict_with_secret_in_error_field(self):
        """Dict errors with secrets in 'error' field are redacted."""
        errors = [
            {"node": "api_call", "error": "Failed with key sk-abc123def456ghi789jkl012", "timestamp": "2024-01-01"},
        ]
        result = redact_secrets_in_list(errors)
        
        assert isinstance(result[0], str)
        assert "[api_call]" in result[0]
        assert "[REDACTED:OPENAI_KEY]" in result[0]
        assert "sk-abc" not in result[0]


class TestNormalizeError:
    """Test the normalize_error function for BUG-6 fix."""
    
    def test_normalizes_string(self):
        """String errors are returned as-is (with redaction)."""
        error = "Simple error message"
        result = normalize_error(error)
        assert result == "Simple error message"
    
    def test_normalizes_dict_with_error_key(self):
        """Dict with 'error' key extracts the error message."""
        error = {"error": "LLM timeout", "timestamp": "2024-01-01"}
        result = normalize_error(error)
        assert result == "LLM timeout"
    
    def test_normalizes_dict_with_node_and_error(self):
        """Dict with 'node' and 'error' keys formats as '[node] error'."""
        error = {"node": "gen_code", "error": "Failed to generate", "timestamp": "2024-01-01"}
        result = normalize_error(error)
        assert result == "[gen_code] Failed to generate"
    
    def test_normalizes_dict_with_message_key(self):
        """Dict with 'message' key (common in exceptions)."""
        error = {"message": "Connection refused", "code": 500}
        result = normalize_error(error)
        assert result == "Connection refused"
    
    def test_normalizes_dict_with_detail_key(self):
        """Dict with 'detail' key (FastAPI style)."""
        error = {"detail": "Not found", "status": 404}
        result = normalize_error(error)
        assert result == "Not found"
    
    def test_normalizes_nested_dict_fallback(self):
        """Dict without standard keys falls back to str(dict)."""
        error = {"custom_key": "some value"}
        result = normalize_error(error)
        assert "custom_key" in result
        assert "some value" in result
    
    def test_normalizes_none(self):
        """None returns '[Unknown error]'."""
        result = normalize_error(None)
        assert result == "[Unknown error]"
    
    def test_normalizes_exception(self):
        """Exception is converted to string."""
        error = ValueError("Invalid value")
        result = normalize_error(error)
        assert "Invalid value" in result
    
    def test_normalizes_list_of_errors(self):
        """List of errors is joined with semicolons."""
        error = ["Error 1", "Error 2", {"error": "Error 3"}]
        result = normalize_error(error)
        assert "Error 1" in result
        assert "Error 2" in result
        assert "Error 3" in result
        assert ";" in result
    
    def test_redacts_secrets_in_dict_error(self):
        """Secrets in dict error messages are redacted."""
        error = {"node": "api", "error": "Failed with key sk-abc123def456ghi789jkl012"}
        result = normalize_error(error)
        assert "[REDACTED:OPENAI_KEY]" in result
        assert "sk-abc" not in result
    
    def test_handles_atexit_error_format(self):
        """Handles the exact format from runtime.py atexit handler."""
        # This is the exact dict format from runtime.py line 249
        error = {
            "node": "atexit",
            "error": "Run interrupted - atexit handler invoked",
            "timestamp": "2024-01-01T00:00:00Z",
        }
        result = normalize_error(error)
        assert "[atexit]" in result
        assert "Run interrupted" in result


class TestRedactDictValues:
    """Test dictionary value redaction."""
    
    def test_redacts_string_values(self):
        """String values containing secrets are redacted."""
        data = {
            "error": "Failed with key sk-abc123def456ghi789jkl012",
            "status": "failed",
        }
        result = redact_dict_values(data)
        
        # Original not modified
        assert "sk-abc" in data["error"]
        
        # New dict has redacted values
        assert "[REDACTED:OPENAI_KEY]" in result["error"]
        assert result["status"] == "failed"
    
    def test_redacts_specific_keys_entirely(self):
        """Specified keys have values fully redacted."""
        data = {
            "api_key": "sk-abc123",
            "message": "some message",
        }
        result = redact_dict_values(data, keys_to_redact=["api_key"])
        assert result["api_key"] == "[REDACTED]"
        assert result["message"] == "some message"


# =============================================================================
# handle_error node tests
# =============================================================================

class TestHandleError:
    """Test handle_error node hardening."""
    
    @pytest.fixture
    def state_with_errors(self):
        """Create a state with errors containing secrets."""
        return WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="Test task",
            run_id="test-run-123",
            provider_code="test_provider",
            errors=[
                "API call failed with key sk-abc123def456ghi789jkl012mno345",
                "Auth header: Bearer eyJhbGciOiJIUzI1NiJ9.test",
                "Generic network error",
            ],
            plan={},
            completed_steps=["some_node"],
        )
    
    def test_sets_failed_and_summary(self, state_with_errors):
        """handle_error sets plan['failed'] and error_summary."""
        result = handle_error(state_with_errors)
        
        assert result.plan["failed"] is True
        assert result.plan["error_count"] == 3
        assert "error_summary" in result.plan
        assert "+2 more" in result.plan["error_summary"]  # 3 errors, show 1 + count
    
    def test_redacts_errors_in_place(self, state_with_errors):
        """Secrets in state.errors are redacted."""
        handle_error(state_with_errors)
        
        # Original list was modified
        assert "sk-abc" not in state_with_errors.errors[0]
        assert "[REDACTED:OPENAI_KEY]" in state_with_errors.errors[0]
        assert "eyJ" not in state_with_errors.errors[1]
        assert "[REDACTED:TOKEN]" in state_with_errors.errors[1]
        # Non-secret errors unchanged
        assert state_with_errors.errors[2] == "Generic network error"
    
    def test_idempotent_multiple_calls(self, state_with_errors):
        """Calling handle_error twice produces same result."""
        result1 = handle_error(state_with_errors)
        result2 = handle_error(result1)
        
        # Same state object
        assert result1 is result2
        
        # Only one completed_steps entry
        assert state_with_errors.completed_steps.count("handle_error") == 1
        
        # Re-entrancy flag set under _internal namespace
        assert state_with_errors.plan["_internal"]["handling_error"] is True
    
    def test_handles_empty_errors(self):
        """handle_error works with no errors (edge case)."""
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="Test",
            errors=[],
            plan={},
        )
        result = handle_error(state)
        
        assert result.plan["failed"] is True
        assert result.plan["error_count"] == 0
        assert "Unknown error" in result.plan["error_summary"]
    
    def test_truncates_long_error_summary(self):
        """Long error messages are truncated in summary."""
        long_error = "x" * 300
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="Test",
            errors=[long_error],
            plan={},
        )
        result = handle_error(state)
        
        # Summary is truncated
        assert len(result.plan["error_summary"]) <= 203  # 200 + "..."
        assert result.plan["error_summary"].endswith("...")
    
    def test_logs_with_context(self, state_with_errors, caplog):
        """handle_error logs with structured context."""
        with caplog.at_level(logging.ERROR):
            handle_error(state_with_errors)
        
        # Check log message
        assert "Workflow error handled" in caplog.text
        # Note: extra fields may not appear in text but are in record
        assert len(caplog.records) >= 1
        record = [r for r in caplog.records if "error handled" in r.message][0]
        assert record.levelno == logging.ERROR
    
    def test_marks_completed_step(self, state_with_errors):
        """handle_error adds itself to completed_steps."""
        assert "handle_error" not in state_with_errors.completed_steps
        handle_error(state_with_errors)
        assert "handle_error" in state_with_errors.completed_steps
    
    def test_preserves_existing_plan_data(self, state_with_errors):
        """handle_error doesn't clobber existing plan data."""
        state_with_errors.plan["existing_key"] = "preserve_me"
        handle_error(state_with_errors)
        assert state_with_errors.plan["existing_key"] == "preserve_me"


class TestHandleErrorEdgeCases:
    """Edge cases and boundary conditions for handle_error."""
    
    def test_no_run_id(self):
        """handle_error works when run_id is None."""
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="Test",
            run_id=None,
            errors=["Some error"],
            plan={},
        )
        result = handle_error(state)
        assert result.plan["failed"] is True
    
    def test_no_provider_code(self):
        """handle_error works when provider_code is None."""
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="Test",
            provider_code=None,
            errors=["Some error"],
            plan={},
        )
        result = handle_error(state)
        assert result.plan["failed"] is True
    
    def test_existing_internal_preserved(self):
        """Existing _internal fields are preserved, handling_error is added."""
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="Test",
            errors=["Error"],
            plan={"_internal": {"other_flag": True}},
        )
        handle_error(state)
        
        assert state.plan["_internal"]["other_flag"] is True
        assert state.plan["_internal"]["handling_error"] is True
    
    def test_handles_mixed_type_errors_bug6(self):
        """BUG-6: handle_error processes mixed string/dict errors without crashing."""
        # This test reproduces the exact crash scenario from production
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="Test",
            errors=[
                "String error first",
                {
                    "node": "atexit",
                    "error": "Run interrupted - atexit handler invoked",
                    "timestamp": "2024-01-01T00:00:00Z",
                },
                {"error": "Another dict error"},
            ],
            plan={},
        )
        
        # This should NOT raise AttributeError: 'list' object has no attribute 'get'
        result = handle_error(state)
        
        # Basic checks
        assert result.plan["failed"] is True
        assert result.plan["error_count"] == 3
        
        # All errors should now be strings (normalized by redact_secrets_in_list)
        assert all(isinstance(e, str) for e in result.errors)
        
        # Summary should be from first error (a string)
        assert "String error first" in result.plan["error_summary"]
    
    def test_handles_dict_error_as_first_error(self):
        """BUG-6: handle_error works when first error is a dict."""
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="Test",
            errors=[
                {
                    "node": "gen_code",
                    "error": "LLM rate limited",
                    "timestamp": "2024-01-01T00:00:00Z",
                },
                "Second error as string",
            ],
            plan={},
        )
        
        result = handle_error(state)
        
        # Should not crash
        assert result.plan["failed"] is True
        
        # Summary should contain the normalized dict error
        # After redact_secrets_in_list, it becomes "[gen_code] LLM rate limited"
        assert "[gen_code]" in result.plan["error_summary"] or "LLM rate limited" in result.plan["error_summary"]


# =============================================================================
# Production Realism Tests (OWASP compliance)
# =============================================================================

class TestLogInjectionSanitization:
    """Test log-injection sanitization per OWASP Logging Cheat Sheet."""
    
    def test_sanitize_removes_newlines(self):
        """CR/LF characters are removed to prevent log injection."""
        text = "Error on line 1\nLine 2\rLine 3\r\nLine 4"
        result = sanitize_for_logging(text)
        assert "\n" not in result
        assert "\r" not in result
        # Content preserved (spaces replace control chars)
        assert "Error on line 1" in result
        assert "Line 2" in result
    
    def test_sanitize_removes_null_bytes(self):
        """NUL and other control characters are removed."""
        text = "Error\x00with\x01null\x08bytes"
        result = sanitize_for_logging(text)
        assert "\x00" not in result
        assert "\x01" not in result
        assert "\x08" not in result
    
    def test_redact_and_sanitize_combined(self):
        """redact_and_sanitize does both secret redaction and log sanitization."""
        text = "Error with key sk-abc123def456ghi789jkl012\nand newline"
        result = redact_and_sanitize(text)
        # Secret redacted
        assert "sk-abc" not in result
        assert "[REDACTED:OPENAI_KEY]" in result
        # Newline sanitized
        assert "\n" not in result
    
    def test_handle_error_sanitizes_log_injection(self):
        """handle_error removes log injection chars from errors."""
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="Test",
            errors=["Error\nwith\nnewlines\rand\rcarriage returns"],
            plan={},
        )
        handle_error(state)
        
        # Errors are sanitized
        assert "\n" not in state.errors[0]
        assert "\r" not in state.errors[0]
        # Summary is also sanitized
        assert "\n" not in state.plan["error_summary"]


class TestPerformanceBounds:
    """Test bounded performance for large inputs."""
    
    def test_truncates_huge_input(self):
        """Input longer than MAX_REDACTION_INPUT_LENGTH is truncated."""
        huge_input = "x" * (MAX_REDACTION_INPUT_LENGTH + 1000)
        result = redact_secrets(huge_input)
        
        # Result is bounded
        assert len(result) <= MAX_REDACTION_INPUT_LENGTH + len("... [TRUNCATED]")
        assert result.endswith("... [TRUNCATED]")
    
    def test_large_error_list_bounded_time(self):
        """Processing large error lists completes in reasonable time."""
        import time
        
        # 100 errors, each 1KB (100KB total)
        large_errors = ["Error " + "x" * 1000 for _ in range(100)]
        
        start = time.time()
        redact_secrets_in_list(large_errors)
        elapsed = time.time() - start
        
        # Should complete in under 1 second (generous bound)
        assert elapsed < 1.0, f"Redaction took {elapsed:.2f}s for 100 errors"
    
    def test_handle_error_with_huge_traceback(self):
        """handle_error handles huge traceback strings gracefully."""
        # Simulate a huge traceback (20KB)
        huge_traceback = "Traceback:\n" + "  File 'test.py', line X\n" * 500
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="Test",
            errors=[huge_traceback],
            plan={},
        )
        
        handle_error(state)
        
        # Error is processed (truncated if over limit)
        assert state.plan["failed"] is True
        # Summary is bounded
        assert len(state.plan["error_summary"]) <= 203
    
    def test_precompiled_patterns_no_recompilation(self):
        """Regex patterns are precompiled (import-time check)."""
        # This test verifies patterns are compiled Pattern objects
        from integration_coworker.security.redaction import _REDACTION_PATTERNS
        import re
        
        for pattern, _ in _REDACTION_PATTERNS:
            assert isinstance(pattern, re.Pattern), f"{pattern} is not precompiled"
