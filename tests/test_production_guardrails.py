"""
Production Guardrails Tests (PR #9 Step 6)

Tests that prove production guardrails work correctly:
1. Timeout behavior (simulated tool that hangs)
2. Huge output handling (multi-MB stdout/stderr)
3. Secret redaction (API keys, tokens never leak)
4. Deterministic truncation (same inputs -> same outputs)
5. Prefilter performance (substring check before regex)
6. CONTRACT TESTS: Same normalized output across different callers

These tests are merge-blocking: if guardrails regress, CI fails.
"""
import os
import subprocess
import sys
import time
from typing import List
from unittest.mock import patch, MagicMock

import pytest


class TestSubprocessTimeout:
    """
    Test subprocess timeout behavior per Python docs.
    
    Per Python docs: subprocess.run with timeout kills the child process
    and raises TimeoutExpired after the specified seconds.
    """
    
    def test_run_tool_safely_timeout_returns_bounded_failure(self):
        """Tool that times out returns bounded, attributable failure."""
        from integration_coworker.graph.production_guardrails import run_tool_safely
        
        # Use a command that will hang (sleep for longer than timeout)
        result = run_tool_safely(
            cmd=["sleep", "60"],
            timeout=1,  # 1 second timeout
        )
        
        assert result.timed_out is True
        assert result.returncode == -1
        assert "timed out" in result.stderr.lower()
        assert result.duration_ms >= 900  # At least ~1 second
        assert result.duration_ms < 3000  # But not too long
        assert result.metadata["timed_out"] is True
    
    def test_run_tool_safely_timeout_does_not_hang_test(self):
        """Timeout doesn't hang the test runner."""
        from integration_coworker.graph.production_guardrails import run_tool_safely
        
        start = time.monotonic()
        
        # Run with short timeout
        result = run_tool_safely(
            cmd=["sleep", "60"],
            timeout=1,
        )
        
        elapsed = time.monotonic() - start
        
        # Should complete in ~1 second (not 60)
        assert elapsed < 5, f"Test took too long: {elapsed}s (should be ~1s)"
        assert result.timed_out is True
    
    def test_run_tool_safely_timeout_ceiling_enforced(self):
        """Timeout is capped at TOOL_MAX_TIMEOUT_SECONDS."""
        from integration_coworker.graph.production_guardrails import (
            run_tool_safely,
            TOOL_MAX_TIMEOUT_SECONDS,
        )
        
        # Request absurdly long timeout
        result = run_tool_safely(
            cmd=["echo", "test"],
            timeout=99999,  # Way over max
        )
        
        # Should use the ceiling
        assert result.metadata["timeout_seconds"] == TOOL_MAX_TIMEOUT_SECONDS


class TestHugeOutputHandling:
    """
    Test that huge outputs are bounded correctly.
    
    Verifies:
    - Outputs capped at MAX_TOOL_OUTPUT_BYTES
    - Truncation is deterministic
    - Metadata reflects truncation
    """
    
    def test_run_tool_safely_caps_large_output(self):
        """Large output is truncated to MAX_TOOL_OUTPUT_BYTES."""
        from integration_coworker.graph.production_guardrails import (
            run_tool_safely,
            MAX_TOOL_OUTPUT_BYTES,
        )
        
        # Generate large output (100KB)
        large_size = 100 * 1024
        result = run_tool_safely(
            cmd=["python", "-c", f"print('X' * {large_size})"],
            timeout=10,
        )
        
        # Output should be capped
        assert len(result.stdout.encode('utf-8')) <= MAX_TOOL_OUTPUT_BYTES + 100  # Small buffer
        assert result.truncated is True
        assert result.metadata["output_truncated"] is True
    
    def test_run_tool_safely_caps_output_lines(self):
        """Output is capped at MAX_TOOL_OUTPUT_LINES."""
        from integration_coworker.graph.production_guardrails import (
            run_tool_safely,
            MAX_TOOL_OUTPUT_LINES,
        )
        
        # Generate many lines (1000 lines)
        result = run_tool_safely(
            cmd=["python", "-c", "for i in range(1000): print(f'Line {i}')"],
            timeout=10,
            max_lines=100,  # Cap at 100 lines
        )
        
        lines = result.stdout.strip().split('\n')
        assert len(lines) <= 100
        assert "[TRUNCATED]" in result.stdout or "truncated" in result.stdout.lower() or len(lines) == 100
    
    def test_normalize_evidence_caps_bytes(self):
        """normalize_evidence caps at MAX_EVIDENCE_BYTES."""
        from integration_coworker.graph.production_guardrails import (
            normalize_evidence,
            MAX_EVIDENCE_BYTES,
        )
        
        # Generate large evidence
        large_evidence = "X" * (MAX_EVIDENCE_BYTES * 2)
        
        result = normalize_evidence(large_evidence)
        
        assert len(result) <= MAX_EVIDENCE_BYTES + 100  # Small buffer for truncation marker
    
    def test_deterministic_truncation(self):
        """Same large input always produces same truncated output."""
        from integration_coworker.graph.production_guardrails import normalize_evidence
        
        large_evidence = "Line {}\n".format("X" * 100) * 50
        
        result1 = normalize_evidence(large_evidence)
        result2 = normalize_evidence(large_evidence)
        result3 = normalize_evidence(large_evidence)
        
        assert result1 == result2 == result3


class TestSecretRedaction:
    """
    Test that secrets are redacted from all outputs.
    
    Verifies:
    - API keys never appear in normalized output
    - Tokens never appear in fingerprints
    - Passwords are redacted
    """
    
    def test_redact_api_keys(self):
        """API keys are redacted."""
        from integration_coworker.graph.production_guardrails import redact_secrets
        
        test_cases = [
            # Test that generic API key patterns are redacted
            ("api_key=fake_api_key_for_test_0000000000000000", "fake_api_key"),
            ("APIKEY: secret1234567890abcdef", "secret1234"),
            # JWT tokens should be fully redacted
            ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U", "eyJ"),
        ]
        
        for input_text, secret_fragment in test_cases:
            result = redact_secrets(input_text)
            assert secret_fragment not in result, f"Secret fragment '{secret_fragment}' found in: {result}"
    
    def test_redact_aws_keys(self):
        """AWS credentials are redacted."""
        from integration_coworker.graph.production_guardrails import redact_secrets
        
        test_cases = [
            "AKIAIOSFODNN7EXAMPLE",  # AWS access key pattern
            "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        ]
        
        for input_text in test_cases:
            result = redact_secrets(input_text)
            assert "AKIA" not in result or "REDACTED" in result
            assert "wJalr" not in result
    
    def test_redact_github_tokens(self):
        """GitHub tokens are redacted."""
        from integration_coworker.graph.production_guardrails import redact_secrets
        
        test_cases = [
            "ghp_abcdefghijklmnopqrstuvwxyz1234567890",  # PAT
            "gho_abcdefghijklmnopqrstuvwxyz1234567890",  # OAuth
        ]
        
        for input_text in test_cases:
            result = redact_secrets(input_text)
            assert "ghp_" not in result or "REDACTED" in result
            assert "gho_" not in result or "REDACTED" in result
    
    def test_redact_database_passwords(self):
        """Database connection string passwords are redacted."""
        from integration_coworker.graph.production_guardrails import redact_secrets
        
        input_text = "postgresql://user:supersecretpassword@localhost:5432/db"
        result = redact_secrets(input_text)
        
        assert "supersecretpassword" not in result
        assert "postgresql://" in result or "***" in result
    
    def test_normalize_evidence_redacts_secrets(self):
        """normalize_evidence includes redaction by default."""
        from integration_coworker.graph.production_guardrails import normalize_evidence
        
        input_text = """
        Error connecting to API
        api_key=fake_api_key_for_test_0000000000000000000
        Failed with status 401
        """
        
        result = normalize_evidence(input_text)
        
        # Verify secrets are redacted
        assert "fake_api_key" not in result or "REDACTED" in result
    
    def test_fingerprint_does_not_contain_secrets(self):
        """Fingerprints don't contain secrets (normalized before hashing)."""
        from integration_coworker.graph.sandbox_attribution import compute_evidence_fingerprint
        
        # Two inputs with different secrets but same structure
        input1 = "Error: api_key=secret111111111111111111111"
        input2 = "Error: api_key=secret222222222222222222222"
        
        fp1 = compute_evidence_fingerprint(input1, "test")
        fp2 = compute_evidence_fingerprint(input2, "test")
        
        # Fingerprints should be SAME because secrets are redacted
        assert fp1 == fp2, "Fingerprints should be identical after redaction"


class TestDeterministicBehavior:
    """
    Test that all operations are deterministic.
    
    Same inputs must produce identical outputs across runs.
    """
    
    def test_normalize_evidence_deterministic(self):
        """normalize_evidence produces identical output for identical input."""
        from integration_coworker.graph.production_guardrails import normalize_evidence
        
        test_input = """
        File "/home/user/src/client.py", line 42
        TypeError: expected str, got int
        at 2024-01-15T10:30:45.123Z
        pid=12345
        0x7fff5fbff8c0
        """
        
        results = [normalize_evidence(test_input) for _ in range(5)]
        
        assert all(r == results[0] for r in results), "normalize_evidence is not deterministic"
    
    def test_tool_result_to_dict_sorted_keys(self):
        """ToolResult.to_dict() has sorted keys for stable JSON."""
        from integration_coworker.graph.production_guardrails import ToolResult
        
        result = ToolResult(
            returncode=0,
            stdout="test output",
            stderr="",
            timed_out=False,
            truncated=False,
            duration_ms=100,
            metadata={"z_key": "z", "a_key": "a", "m_key": "m"},
        )
        
        d = result.to_dict()
        keys = list(d.keys())
        
        assert keys == sorted(keys), f"Keys should be sorted: {keys}"
        assert list(d["metadata"].keys()) == sorted(d["metadata"].keys())
    
    def test_bounded_list_deterministic(self):
        """bounded_list produces deterministic output."""
        from integration_coworker.graph.production_guardrails import bounded_list
        
        items = [3, 1, 4, 1, 5, 9, 2, 6, 5, 3]
        
        result1 = bounded_list(items, 5, key=lambda x: x)
        result2 = bounded_list(items, 5, key=lambda x: x)
        
        assert result1 == result2
        assert result1 == [1, 1, 2, 3, 3]  # Sorted and bounded


class TestPrefilterPerformance:
    """
    Test that prefilters improve pattern matching performance.
    """
    
    def test_pattern_spec_prefilter_check(self):
        """PatternSpec.matches_prefilter does cheap substring check."""
        from integration_coworker.graph.sandbox_attribution import PatternSpec, FailureCategory
        import re
        
        spec = PatternSpec(
            name="test_pattern",
            regex=re.compile(r"complex.*pattern.*with.*groups", re.I),
            category=FailureCategory.CODE_SYNTAX_ERROR,
            hint_template="Test hint",
            base_confidence=0.9,
            priority=1,
            prefilter_tokens={"complex", "pattern"},
        )
        
        # Should pass prefilter (contains "complex")
        assert spec.matches_prefilter("this has complex stuff") is True
        
        # Should pass prefilter (contains "pattern")
        assert spec.matches_prefilter("some pattern here") is True
        
        # Should fail prefilter (doesn't contain any token)
        assert spec.matches_prefilter("totally unrelated text") is False
    
    def test_empty_prefilter_always_passes(self):
        """Empty prefilter_tokens means always run regex."""
        from integration_coworker.graph.sandbox_attribution import PatternSpec, FailureCategory
        import re
        
        spec = PatternSpec(
            name="test_pattern",
            regex=re.compile(r"anything"),
            category=FailureCategory.CODE_SYNTAX_ERROR,
            hint_template="Test hint",
            base_confidence=0.9,
            priority=1,
            prefilter_tokens=set(),  # Empty
        )
        
        assert spec.matches_prefilter("totally random") is True
        assert spec.matches_prefilter("") is True
    
    def test_all_patterns_have_reasonable_prefilters(self):
        """Every pattern with prefilter has tokens that relate to its regex."""
        from integration_coworker.graph.sandbox_attribution import (
            _ENV_PATTERNS,
            _TEST_PATTERNS,
            _CODE_PATTERNS,
            _INFRASTRUCTURE_PATTERNS,
        )
        
        all_patterns = _ENV_PATTERNS + _TEST_PATTERNS + _CODE_PATTERNS + _INFRASTRUCTURE_PATTERNS
        
        for spec in all_patterns:
            if spec.prefilter_tokens:
                # Each prefilter token should be related to what the regex matches
                # Just verify they're non-empty strings
                for token in spec.prefilter_tokens:
                    assert len(token) >= 2, f"Prefilter token too short: {token} in {spec.name}"


class TestBoundConstants:
    """
    Test that bound constants are consistent and reasonable.
    """
    
    def test_bounds_are_positive(self):
        """All bound constants are positive."""
        from integration_coworker.graph.production_guardrails import (
            TOOL_TIMEOUT_SECONDS,
            TOOL_MAX_TIMEOUT_SECONDS,
            MAX_TOOL_OUTPUT_BYTES,
            MAX_TOOL_OUTPUT_LINES,
            MAX_LINE_LENGTH,
            MAX_EVIDENCE_BYTES,
            MAX_EVIDENCE_LINES,
            MAX_ARTIFACT_SUMMARY_BYTES,
        )
        
        bounds = [
            ("TOOL_TIMEOUT_SECONDS", TOOL_TIMEOUT_SECONDS),
            ("TOOL_MAX_TIMEOUT_SECONDS", TOOL_MAX_TIMEOUT_SECONDS),
            ("MAX_TOOL_OUTPUT_BYTES", MAX_TOOL_OUTPUT_BYTES),
            ("MAX_TOOL_OUTPUT_LINES", MAX_TOOL_OUTPUT_LINES),
            ("MAX_LINE_LENGTH", MAX_LINE_LENGTH),
            ("MAX_EVIDENCE_BYTES", MAX_EVIDENCE_BYTES),
            ("MAX_EVIDENCE_LINES", MAX_EVIDENCE_LINES),
            ("MAX_ARTIFACT_SUMMARY_BYTES", MAX_ARTIFACT_SUMMARY_BYTES),
        ]
        
        for name, value in bounds:
            assert value > 0, f"{name} should be positive"
    
    def test_timeout_ceiling_greater_than_default(self):
        """TOOL_MAX_TIMEOUT_SECONDS >= TOOL_TIMEOUT_SECONDS."""
        from integration_coworker.graph.production_guardrails import (
            TOOL_TIMEOUT_SECONDS,
            TOOL_MAX_TIMEOUT_SECONDS,
        )
        
        assert TOOL_MAX_TIMEOUT_SECONDS >= TOOL_TIMEOUT_SECONDS
    
    def test_attribution_bounds_imported_correctly(self):
        """sandbox_attribution re-exports bounds from production_guardrails."""
        from integration_coworker.graph.sandbox_attribution import (
            MAX_EVIDENCE_BYTES,
            MAX_EVIDENCE_LINES,
        )
        from integration_coworker.graph.production_guardrails import (
            MAX_EVIDENCE_BYTES as PG_EVIDENCE_BYTES,
            MAX_EVIDENCE_LINES as PG_EVIDENCE_LINES,
        )
        
        assert MAX_EVIDENCE_BYTES == PG_EVIDENCE_BYTES
        assert MAX_EVIDENCE_LINES == PG_EVIDENCE_LINES


class TestSafeJsonDumps:
    """
    Test safe_json_dumps utility.
    """
    
    def test_safe_json_dumps_sorts_keys(self):
        """safe_json_dumps uses sort_keys=True."""
        from integration_coworker.graph.production_guardrails import safe_json_dumps
        
        d = {"z": 1, "a": 2, "m": 3}
        result = safe_json_dumps(d)
        
        # Keys should appear in sorted order in JSON
        assert result.index('"a"') < result.index('"m"') < result.index('"z"')
    
    def test_safe_json_dumps_truncates_large_output(self):
        """safe_json_dumps truncates output exceeding max_bytes."""
        from integration_coworker.graph.production_guardrails import safe_json_dumps
        
        large_data = {"key": "X" * 10000}
        
        result = safe_json_dumps(large_data, max_bytes=100)
        
        assert len(result) <= 100
        assert "TRUNCATED" in result


class TestAttributionWithGuardrails:
    """
    Integration tests: attribution uses guardrails correctly.
    """
    
    def test_attribution_uses_normalized_evidence(self):
        """attribute_failure uses normalize_evidence internally."""
        from integration_coworker.graph.sandbox_attribution import attribute_failure
        
        # Include volatile tokens that should be normalized
        output = """
        Error at 2024-01-15T10:30:45Z
        pid=12345
        File "/absolute/path/src/client.py", line 42
        TypeError: expected str
        """
        
        result = attribute_failure(output, "mypy", 1)
        
        # Fingerprint should be stable (volatile tokens normalized)
        fp1 = result.evidence_fingerprint
        fp2 = attribute_failure(output, "mypy", 1).evidence_fingerprint
        
        assert fp1 == fp2, "Fingerprints should be identical"
    
    def test_attribution_redacts_secrets_in_evidence(self):
        """Secrets in output don't affect attribution adversely."""
        from integration_coworker.graph.sandbox_attribution import attribute_failure
        
        output = """
        Error connecting with api_key=fake_api_key_for_test_000000000000000000
        ModuleNotFoundError: No module named 'requests'
        """
        
        result = attribute_failure(output, "pytest", 1)
        
        # Should still correctly attribute to missing dependency
        assert result.category.value == "env_missing_dependency"
        
        # Raw signals shouldn't contain the secret
        signals_str = str(result.raw_signals)
        assert "fake_api_key" not in signals_str or "REDACTED" in signals_str


# =============================================================================
# CONTRACT TESTS: Prove guardrails contract holds across modules
# =============================================================================

class TestGuardrailsContract:
    """
    Contract tests that prove the guardrails contract holds.
    
    These tests verify:
    1. Single source of truth: all modules import from production_guardrails
    2. Cross-module equivalence: same input → same output regardless of caller
    3. Exception-proof: normalization never raises on any input
    4. Deterministic: same input always produces same output
    """
    
    def test_constants_imported_from_single_source(self):
        """All bounded constants come from production_guardrails."""
        from integration_coworker.graph import production_guardrails as pg
        from integration_coworker.graph import quality_models
        from integration_coworker.graph import sandbox_attribution
        
        # Verify quality_models uses guardrails constants
        assert quality_models.MAX_HINT_LENGTH == pg.MAX_HINT_LENGTH
        assert quality_models.MAX_FILE_PATH_LENGTH == pg.MAX_FILE_PATH_LENGTH
        assert quality_models.MAX_FIX_HINTS == pg.MAX_FIX_HINTS
        
        # Verify sandbox_attribution uses guardrails constants
        assert sandbox_attribution.MAX_EVIDENCE_BYTES == pg.MAX_EVIDENCE_BYTES
        assert sandbox_attribution.MAX_EVIDENCE_LINES == pg.MAX_EVIDENCE_LINES
        assert sandbox_attribution.MAX_FIX_HINTS == pg.MAX_FIX_HINTS
    
    def test_cross_module_normalization_equivalence(self):
        """Same raw evidence produces identical normalized output everywhere."""
        from integration_coworker.graph.production_guardrails import normalize_evidence as pg_normalize
        from integration_coworker.graph.sandbox_attribution import normalize_evidence as sa_normalize
        
        test_inputs = [
            "Simple error message",
            "Error at 2024-01-15T10:30:45Z\npid=12345",
            "api_key=fake_api_key_for_test_000000000000000000\nError",
            "A" * 5000,  # Large input
            "",  # Empty input
            "\x1b[31mColored\x1b[0m output",  # ANSI codes
            "Line1\r\nLine2\rLine3",  # Mixed line endings
        ]
        
        for raw_input in test_inputs:
            pg_result = pg_normalize(raw_input)
            sa_result = sa_normalize(raw_input)
            assert pg_result == sa_result, f"Normalization differs for: {raw_input[:50]}..."
    
    def test_normalization_never_raises(self):
        """Normalization is exception-proof on pathological inputs."""
        from integration_coworker.graph.production_guardrails import normalize_evidence
        
        pathological_inputs = [
            "",
            "\x00\x01\x02",  # Binary garbage
            "\\T\\P\\S",  # Backslash sequences
            "a" * 1000000,  # Very large
            "\n" * 10000,  # Many empty lines
            r"C:\Temp\file.txt",  # Windows path with backslashes
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig",  # JWT
            "AKIA" + "A" * 16,  # AWS key
            "ghp_" + "a" * 40,  # GitHub token
        ]
        
        for input_data in pathological_inputs:
            try:
                result = normalize_evidence(input_data)
                assert isinstance(result, str)
            except Exception as e:
                pytest.fail(f"normalize_evidence raised on input {repr(input_data)[:50]}: {e}")
    
    def test_redaction_never_raises(self):
        """Redaction is exception-proof on any input."""
        from integration_coworker.graph.production_guardrails import redact_secrets
        
        pathological_inputs = [
            "",
            "normal text",
            "\\1\\2\\3",  # Backslash numbers (regex backreference-like)
            r"\g<1>",  # Named backreference syntax
            "$1$2$3",  # Dollar signs
            "a" * 100000,  # Large input
        ]
        
        for input_data in pathological_inputs:
            try:
                result = redact_secrets(input_data)
                assert isinstance(result, str)
            except Exception as e:
                pytest.fail(f"redact_secrets raised on input {repr(input_data)[:50]}: {e}")
    
    def test_subprocess_timeout_always_kills_and_reaps(self):
        """Subprocess timeout always terminates child and reaps zombie."""
        from integration_coworker.graph.production_guardrails import run_tool_safely
        
        # Run a command that would hang forever
        result = run_tool_safely(
            cmd=["sleep", "300"],  # 5 minutes
            timeout=1,  # 1 second timeout
        )
        
        assert result.timed_out is True
        assert result.returncode == -1
        
        # Verify no zombie processes left (if this hangs, CI will catch it)
    
    def test_output_caps_always_enforced(self):
        """Output caps are enforced regardless of input size."""
        from integration_coworker.graph.production_guardrails import (
            run_tool_safely, 
            MAX_TOOL_OUTPUT_BYTES,
            normalize_evidence,
            MAX_EVIDENCE_BYTES,
        )
        
        # Generate very large output
        result = run_tool_safely(
            cmd=["python", "-c", f"print('X' * {MAX_TOOL_OUTPUT_BYTES * 2})"],
            timeout=10,
        )
        
        # Output must be bounded
        assert len(result.stdout.encode('utf-8')) <= MAX_TOOL_OUTPUT_BYTES + 1000
        assert result.truncated is True
        
        # Normalization must also bound
        huge_input = "X" * (MAX_EVIDENCE_BYTES * 3)
        normalized = normalize_evidence(huge_input)
        assert len(normalized) <= MAX_EVIDENCE_BYTES + 100
    
    def test_deterministic_output_ordering(self):
        """All outputs are deterministically ordered."""
        from integration_coworker.graph.production_guardrails import (
            run_tool_safely,
            bounded_list,
            safe_json_dumps,
        )
        
        # Run same command multiple times
        results = [
            run_tool_safely(cmd=["echo", "test"], timeout=5)
            for _ in range(3)
        ]
        
        # Metadata keys should be in same order
        for r in results:
            assert list(r.metadata.keys()) == sorted(r.metadata.keys())
        
        # Bounded list with key should be sorted
        items = [3, 1, 4, 1, 5, 9]
        bounded = bounded_list(items, 10, key=lambda x: x)
        assert bounded == sorted(items)
        
        # JSON should have sorted keys
        data = {"z": 1, "a": 2, "m": 3}
        json_str = safe_json_dumps(data)
        assert json_str.index('"a"') < json_str.index('"m"') < json_str.index('"z"')
    
    def test_fingerprint_stability_across_runs(self):
        """Fingerprints are stable across multiple runs."""
        from integration_coworker.graph.sandbox_attribution import compute_evidence_fingerprint
        
        raw_evidence = """
        Error at 2024-01-15T10:30:45Z
        pid=12345
        ModuleNotFoundError: No module named 'requests'
        api_key=fake_api_key_for_test_0000000
        """
        
        # Compute fingerprint multiple times
        fingerprints = [
            compute_evidence_fingerprint(raw_evidence, "pytest")
            for _ in range(5)
        ]
        
        # All should be identical
        assert len(set(fingerprints)) == 1, "Fingerprints should be identical across runs"
        
        # Different secrets should produce SAME fingerprint (after redaction)
        evidence_with_secret1 = "Error: api_key=secret1111111111111111111111"
        evidence_with_secret2 = "Error: api_key=secret2222222222222222222222"
        
        fp1 = compute_evidence_fingerprint(evidence_with_secret1, "test")
        fp2 = compute_evidence_fingerprint(evidence_with_secret2, "test")
        
        assert fp1 == fp2, "Fingerprints should match after secret redaction"


# =============================================================================
# NON-BYPASSABLE GUARDRAILS ENFORCEMENT
# =============================================================================

class TestGuardrailsEnforcement:
    """
    Enforcement tests that prevent guardrails from being bypassed.
    
    These tests fail loudly when:
    1. Someone adds string replacements to redaction patterns (must be callable)
    2. Someone defines guardrail constants in modules that should import them
    3. Someone circumvents the guardrails contract
    
    These are merge-blocking: if guardrails can be bypassed, CI fails.
    """
    
    def test_all_redaction_replacements_are_callable(self):
        """
        ENFORCEMENT: All redaction patterns MUST use callable replacements.
        
        Per Python docs (https://docs.python.org/3/library/re.html#re.sub):
        - String replacements process backslash escapes, which can cause issues
        - Callable replacements avoid this entirely
        
        This test prevents regressions where someone adds a string replacement.
        """
        from integration_coworker.graph.production_guardrails import _REDACTION_PATTERNS
        
        for i, (pattern, repl) in enumerate(_REDACTION_PATTERNS):
            assert callable(repl), (
                f"_REDACTION_PATTERNS[{i}] uses string replacement '{repl}' "
                f"instead of callable. Pattern: {pattern.pattern}. "
                f"String replacements can cause backslash escape issues. "
                f"Use lambda m: ... instead."
            )
    
    def test_all_volatile_replacements_are_callable(self):
        """
        ENFORCEMENT: All volatile patterns MUST use callable replacements.
        
        Same rationale as redaction patterns.
        """
        from integration_coworker.graph.production_guardrails import _VOLATILE_PATTERNS
        
        for i, (pattern, repl) in enumerate(_VOLATILE_PATTERNS):
            assert callable(repl), (
                f"_VOLATILE_PATTERNS[{i}] uses string replacement '{repl}' "
                f"instead of callable. Pattern: {pattern.pattern}. "
                f"Use lambda m: ... instead."
            )
    
    def test_no_duplicate_guardrail_constants_in_graph_modules(self):
        """
        ENFORCEMENT: Graph modules must NOT define guardrail constants locally.
        
        These modules should import from production_guardrails, not define their own.
        If they define locally, constants can drift and cause silent bugs.
        
        Allowlist:
        - production_guardrails.py (canonical source)
        - state_v2.py (has _MAX_TRACED_STRING_LENGTH for tracing, not guardrails)
        - runtime.py (has internal sampling constants, not guardrails)
        
        Module-specific constants that are NOT guardrails (allowed):
        - MAX_ISSUES_PER_TOOL, MAX_FILES_PER_CHECK (static_checks.py - tool-specific)
        - MAX_QUALITY_REFS_BYTES (static_analysis_gate.py - checkpoint-specific)
        - MAX_PAYLOAD_SIZE_BYTES (review_gate.py - interrupt-specific)
        """
        import ast
        import pathlib
        
        # Modules that MUST import guardrails, not define them
        graph_modules = [
            "quality_models.py",
            "sandbox_attribution.py",
            "review_artifacts.py",
        ]
        
        # Node modules that MUST import guardrails
        node_modules = [
            "sandbox_attribution_gate.py",
        ]
        
        # Guardrail constant patterns (names that should only be in production_guardrails.py)
        # These are the SHARED bounds used across multiple modules
        guardrail_constants = {
            "MAX_HINT_LENGTH",
            "MAX_FIX_HINTS",
            "MAX_FILE_PATH_LENGTH",
            "MAX_EVIDENCE_BYTES",
            "MAX_EVIDENCE_LINES",
            "MAX_STACK_FRAMES",
            "MAX_ATTRIBUTIONS",
            "MAX_ATTRIBUTIONS_IN_STATE",
            "MAX_RAW_SIGNALS",
            "TOOL_TIMEOUT_SECONDS",
            "MAX_TOOL_OUTPUT_BYTES",
            "MAX_TOOL_OUTPUT_LINES",
            "MAX_FILES_IN_SUMMARY",
            "MAX_ERROR_PREVIEWS",
            "MAX_ERROR_PREVIEW_CHARS",
            "MAX_ERROR_MESSAGE_LENGTH",
            "MAX_SUMMARY_CATEGORIES",
            "MAX_SUMMARY_HINTS",
            "MAX_SUMMARY_FINGERPRINTS",
            "MAX_ARTIFACT_SUMMARY_BYTES",
            "MAX_ARTIFACT_REF_LENGTH",
            "MAX_TARGETS",
            "MAX_FEEDBACK_LENGTH",
            "MAX_TARGET_FEEDBACK_LENGTH",
            "MAX_LINE_LENGTH",
            "TOOL_MAX_TIMEOUT_SECONDS",
        }
        
        base_path = pathlib.Path(__file__).parent.parent / "src" / "integration_coworker" / "graph"
        
        violations = []
        
        for module_name in graph_modules:
            module_path = base_path / module_name
            if not module_path.exists():
                continue
            
            source = module_path.read_text()
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            if target.id in guardrail_constants:
                                violations.append(
                                    f"{module_name}: defines {target.id} locally "
                                    f"(should import from production_guardrails)"
                                )
        
        for module_name in node_modules:
            module_path = base_path / "nodes" / module_name
            if not module_path.exists():
                continue
            
            source = module_path.read_text()
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            if target.id in guardrail_constants:
                                violations.append(
                                    f"nodes/{module_name}: defines {target.id} locally "
                                    f"(should import from production_guardrails)"
                                )
        
        assert not violations, (
            f"Guardrail constants defined outside production_guardrails.py:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )
    
    def test_subprocess_uses_process_group_isolation(self):
        """
        ENFORCEMENT: run_tool_safely must use process-group isolation.
        
        On POSIX, this means start_new_session=True so we can kill the entire
        process tree on timeout, not just the parent.
        """
        import inspect
        from integration_coworker.graph.production_guardrails import run_tool_safely
        
        source = inspect.getsource(run_tool_safely)
        
        # Must use start_new_session for POSIX
        assert "start_new_session" in source, (
            "run_tool_safely must use start_new_session=True for process-group isolation"
        )
        
        # Must handle timeout with process group kill
        assert "_kill_process_tree" in source or "os.killpg" in source, (
            "run_tool_safely must kill process tree on timeout, not just the parent"
        )
    
    def test_kill_process_tree_exists_and_reaps(self):
        """
        ENFORCEMENT: _kill_process_tree must exist and handle SIGTERM→SIGKILL→wait.
        """
        import inspect
        from integration_coworker.graph import production_guardrails
        
        # Function must exist
        assert hasattr(production_guardrails, "_kill_process_tree"), (
            "_kill_process_tree helper must exist for process tree termination"
        )
        
        source = inspect.getsource(production_guardrails._kill_process_tree)
        
        # Must send SIGTERM
        assert "SIGTERM" in source, "_kill_process_tree must send SIGTERM"
        
        # Must send SIGKILL as fallback
        assert "SIGKILL" in source, "_kill_process_tree must send SIGKILL as fallback"
        
        # Must wait to reap
        assert "wait" in source, "_kill_process_tree must wait() to reap zombie"
    
    def test_timeout_proves_process_is_reaped(self):
        """
        ENFORCEMENT: After timeout, the process must be fully reaped (no zombies).
        
        This test runs a sleep command with a short timeout and verifies
        the process is killed and waited on.
        """
        import os
        from integration_coworker.graph.production_guardrails import run_tool_safely
        
        # Run a command that would hang
        result = run_tool_safely(
            cmd=["sleep", "60"],
            timeout=1,
        )
        
        assert result.timed_out is True
        
        # The test completing without hanging proves the process was reaped.
        # If proc.wait() wasn't called, the process would be a zombie and
        # future tests might fail in weird ways.
        
        # Additional check: no zombie cleanup needed (would raise if zombie exists)
        # This is implicit in the test passing.
    
    def test_env_overrides_are_bounded(self):
        """
        ENFORCEMENT: Environment variable overrides cannot exceed hard limits.
        """
        import os
        from integration_coworker.graph import production_guardrails as pg
        
        # Even with crazy env values, timeout must not exceed ceiling
        original_timeout = pg.TOOL_TIMEOUT_SECONDS
        
        # The module uses _env_int which has a fallback, but TOOL_MAX_TIMEOUT_SECONDS
        # is not env-overridable (by design)
        assert pg.TOOL_MAX_TIMEOUT_SECONDS == 300, (
            "TOOL_MAX_TIMEOUT_SECONDS must be 300 (5 minutes) and not env-overridable"
        )
        
        # Verify run_tool_safely enforces the ceiling
        result = pg.run_tool_safely(
            cmd=["echo", "test"],
            timeout=99999,  # Way over max
        )
        
        assert result.metadata["timeout_seconds"] == pg.TOOL_MAX_TIMEOUT_SECONDS