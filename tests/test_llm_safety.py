"""
Tests for llm/safety.py - System prompt hardening.

v2: Tests for FT-SEC-001 from V2 Implementation Plan Section 3.9
"""
import pytest

from integration_coworker.llm.safety import (
    SAFETY_PREAMBLE,
    harden_system_prompt,
)


class TestSafetyPreamble:
    """Tests for the safety preamble constant."""

    def test_preamble_exists(self):
        """Test that safety preamble is defined."""
        assert SAFETY_PREAMBLE is not None
        assert len(SAFETY_PREAMBLE) > 100  # Should be substantial

    def test_preamble_contains_key_rules(self):
        """Test that safety preamble contains key rules."""
        assert "IMPORTANT SAFETY RULES" in SAFETY_PREAMBLE
        assert "IGNORE" in SAFETY_PREAMBLE
        assert "credentials" in SAFETY_PREAMBLE.lower() or "secrets" in SAFETY_PREAMBLE.lower()

    def test_preamble_warns_about_injection(self):
        """Test that preamble warns about prompt injection patterns."""
        assert "ignore previous instructions" in SAFETY_PREAMBLE.lower()

    def test_preamble_ends_with_separator(self):
        """Test that preamble ends with a separator for readability."""
        assert "---" in SAFETY_PREAMBLE


class TestHardenSystemPrompt:
    """Tests for the harden_system_prompt function."""

    def test_harden_with_prompt(self):
        """Test hardening with an existing prompt."""
        original = "You are a helpful code assistant."
        hardened = harden_system_prompt(original)
        
        # Should contain both preamble and original
        assert "IMPORTANT SAFETY RULES" in hardened
        assert original in hardened
        
        # Preamble should come before original
        preamble_pos = hardened.find("IMPORTANT SAFETY RULES")
        original_pos = hardened.find(original)
        assert preamble_pos < original_pos

    def test_harden_with_none(self):
        """Test hardening with None prompt."""
        hardened = harden_system_prompt(None)
        
        # Should return just the preamble (stripped)
        assert "IMPORTANT SAFETY RULES" in hardened
        assert hardened == SAFETY_PREAMBLE.strip()

    def test_harden_with_empty_string(self):
        """Test hardening with empty string."""
        hardened = harden_system_prompt("")
        
        # Empty string is falsy, should return preamble
        assert "IMPORTANT SAFETY RULES" in hardened

    def test_harden_preserves_multiline_prompt(self):
        """Test that multiline prompts are preserved."""
        original = """You are a code generation assistant.
        
Follow these rules:
1. Generate clean Python code
2. Include docstrings
3. Add type hints"""
        
        hardened = harden_system_prompt(original)
        
        # All lines should be preserved
        assert "code generation assistant" in hardened
        assert "Generate clean Python code" in hardened
        assert "Add type hints" in hardened

    def test_harden_with_special_characters(self):
        """Test that prompts with special characters are preserved."""
        original = "Generate code with {placeholders} and $variables and 'quotes'"
        hardened = harden_system_prompt(original)
        
        assert original in hardened

    def test_harden_idempotency(self):
        """Test that hardening is not idempotent (preamble is added each time)."""
        original = "You are a helpful assistant."
        hardened_once = harden_system_prompt(original)
        hardened_twice = harden_system_prompt(hardened_once)
        
        # Second hardening adds another preamble (caller's responsibility to not re-harden)
        # This is by design - the function always prepends
        assert len(hardened_twice) > len(hardened_once)


class TestSecurityCoverage:
    """Tests to verify security coverage of the preamble."""

    def test_covers_code_generation_context(self):
        """Test preamble mentions code generation context."""
        assert "code" in SAFETY_PREAMBLE.lower()
        assert "generate" in SAFETY_PREAMBLE.lower() or "generation" in SAFETY_PREAMBLE.lower()

    def test_covers_credential_safety(self):
        """Test preamble mentions credential safety."""
        preamble_lower = SAFETY_PREAMBLE.lower()
        assert any(word in preamble_lower for word in ["credentials", "api keys", "secrets"])

    def test_covers_network_safety(self):
        """Test preamble mentions network safety."""
        preamble_lower = SAFETY_PREAMBLE.lower()
        assert "network" in preamble_lower or "url" in preamble_lower

    def test_covers_file_safety(self):
        """Test preamble mentions file safety."""
        preamble_lower = SAFETY_PREAMBLE.lower()
        assert "file" in preamble_lower or "directory" in preamble_lower

    def test_covers_injection_detection(self):
        """Test preamble helps detect injection attempts."""
        preamble_lower = SAFETY_PREAMBLE.lower()
        # Should mention how to handle injection-like text
        assert "ignore" in preamble_lower
        assert "instructions" in preamble_lower
