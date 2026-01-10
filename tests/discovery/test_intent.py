"""
Unit tests for discovery/intent.py - Intent Analysis

Tests the heuristic intent extraction logic without any HTTP calls.
"""

import pytest

from integration_coworker.discovery.intent import (
    IntentAnalysis,
    analyze_discovery_intent,
    _extract_explicit_provider,
    _extract_keywords,
    _infer_providers_from_keywords,
)


class TestExtractExplicitProvider:
    """Tests for explicit provider extraction patterns."""
    
    @pytest.mark.parametrize("task,expected", [
        # "from X" pattern
        ("Create a video using Sora from OpenAI", "openai"),
        ("Send email from SendGrid", "sendgrid.com"),
        ("Get data from Stripe", "stripe.com"),
        
        # "using X" pattern
        ("Create a video using OpenAI", "openai"),
        ("Send SMS using Twilio", "twilio.com"),
        ("Process payment using Stripe", "stripe.com"),
        
        # "with X" pattern
        ("Build chatbot with ChatGPT", "openai"),
        ("Send notifications with Twilio", "twilio.com"),
        ("Accept payments with Stripe's API", "stripe.com"),
        
        # "via X" pattern
        ("Upload file via Dropbox", "dropbox.com"),
        ("Send message via Slack", "slack.com"),
        
        # "X API" pattern
        ("Use the OpenAI API to generate text", "openai"),
        ("Call the Stripe API for payments", "stripe.com"),
        ("Integrate with GitHub API", "github.com"),
        
        # No explicit provider
        ("Send a text message to customer", None),
        ("Process credit card payment", None),
        ("Upload a file to cloud storage", None),
    ])
    def test_extract_explicit_provider(self, task, expected):
        """Test explicit provider extraction from various patterns."""
        result = _extract_explicit_provider(task)
        if expected is None:
            assert result is None
        else:
            assert result is not None
            # Normalize for comparison (remove common suffixes)
            result_norm = result.lower().replace(".com", "").replace(".io", "")
            expected_norm = expected.lower().replace(".com", "").replace(".io", "")
            assert result_norm == expected_norm or expected_norm in result_norm


class TestExtractKeywords:
    """Tests for keyword extraction."""
    
    def test_basic_extraction(self):
        """Test basic keyword extraction."""
        keywords = _extract_keywords("Create a video using Sora from OpenAI")
        
        # Should include meaningful words
        assert "video" in keywords
        assert "sora" in keywords
        assert "openai" in keywords
        
        # Should NOT include stopwords
        assert "a" not in keywords
        assert "using" not in keywords
        assert "from" not in keywords
    
    def test_deduplication(self):
        """Test that duplicate keywords are removed."""
        keywords = _extract_keywords("payment payment processing for payments")
        
        # Should dedupe "payment"
        assert keywords.count("payment") == 1
        assert "processing" in keywords
    
    def test_minimum_length(self):
        """Test that very short words are filtered."""
        keywords = _extract_keywords("I am on to do it now")
        
        # Short words should be filtered
        assert "i" not in keywords
        assert "am" not in keywords
        assert "on" not in keywords
        assert "to" not in keywords
        assert "do" not in keywords
        assert "it" not in keywords


class TestInferProvidersFromKeywords:
    """Tests for provider inference from keywords."""
    
    @pytest.mark.parametrize("keywords,expected_providers", [
        # Payment keywords -> Stripe, PayPal
        (["payment", "checkout"], ["stripe.com"]),
        (["subscription", "billing"], ["stripe.com"]),
        
        # Communication keywords -> Twilio, SendGrid
        (["sms", "text"], ["twilio.com"]),
        (["email", "mail"], ["sendgrid.com"]),
        
        # AI keywords -> OpenAI
        (["ai", "llm", "chat"], ["openai.com"]),
        (["gpt", "embedding"], ["openai.com"]),
        
        # Storage keywords -> Dropbox, AWS
        (["file", "upload"], ["dropbox.com"]),
        (["storage"], ["amazonaws.com"]),
        
        # No matching keywords
        (["xyz", "abc"], []),
    ])
    def test_infer_providers(self, keywords, expected_providers):
        """Test provider inference from domain keywords."""
        inferred = _infer_providers_from_keywords(keywords)
        
        for provider in expected_providers:
            assert provider in inferred


class TestAnalyzeDiscoveryIntent:
    """Integration tests for the full intent analysis."""
    
    def test_explicit_provider_high_confidence(self):
        """Explicit provider mention should give high confidence."""
        intent = analyze_discovery_intent("Create a video using Sora from OpenAI")
        
        assert intent.has_explicit_provider
        assert "openai" in intent.explicit_provider.lower()
        assert intent.confidence >= 0.8
    
    def test_inferred_provider_medium_confidence(self):
        """Inferred provider should give medium confidence."""
        intent = analyze_discovery_intent("Process credit card payment with subscription billing")
        
        assert not intent.has_explicit_provider
        assert len(intent.inferred_providers) > 0
        assert "stripe.com" in intent.inferred_providers
        assert 0.4 <= intent.confidence <= 0.8
    
    def test_no_signals_low_confidence(self):
        """No recognizable signals should give low confidence."""
        intent = analyze_discovery_intent("Do something with some API")
        
        assert not intent.has_explicit_provider
        # Confidence should be low but not zero (we extracted some keywords)
        assert intent.confidence < 0.5
    
    def test_empty_task(self):
        """Empty task should return minimal result."""
        intent = analyze_discovery_intent("")
        
        assert intent.confidence < 0.2
        assert not intent.has_explicit_provider
        assert len(intent.keywords) == 0
    
    def test_best_provider_hint_explicit_preferred(self):
        """best_provider_hint should prefer explicit over inferred."""
        intent = analyze_discovery_intent("Send payment notification using Stripe")
        
        # Should prefer explicit "stripe" over inferred from "payment"
        hint = intent.best_provider_hint
        assert hint is not None
        assert "stripe" in hint.lower()
    
    def test_best_provider_hint_falls_back_to_inferred(self):
        """best_provider_hint should fall back to inferred when no explicit."""
        intent = analyze_discovery_intent("Process a credit card payment")
        
        hint = intent.best_provider_hint
        # Should infer from "payment" keyword
        assert hint is not None or len(intent.inferred_providers) > 0
    
    @pytest.mark.parametrize("task", [
        "Create a video using Sora from OpenAI",
        "Process payment with Stripe",
        "Send SMS notification via Twilio",
        "Upload file to Dropbox",
        "Create GitHub issue",
    ])
    def test_common_tasks_have_provider_hint(self, task):
        """Common task patterns should resolve to a provider hint."""
        intent = analyze_discovery_intent(task)
        
        assert intent.best_provider_hint is not None, f"No provider hint for: {task}"
        assert intent.confidence > 0.5, f"Low confidence for: {task}"
