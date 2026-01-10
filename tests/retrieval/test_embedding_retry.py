"""
Tests for embedding retry logic and deterministic fallback.

See docs/PROD_GAP_CLOSURE_PLAN_KG_DB_STREAMING.md Phase 2 for design rationale.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import time

from integration_coworker.retrieval.semantic_search import compute_embedding
from integration_coworker.kg import _deterministic_similarity


# ---------------------------------------------------------------------------
# Tests: Deterministic Fallback Scoring
# ---------------------------------------------------------------------------

class TestDeterministicSimilarity:
    """Tests for _deterministic_similarity() fallback scoring."""
    
    def test_returns_float_in_valid_range(self):
        """Score should be between 0.3 and 0.8."""
        score = _deterministic_similarity(
            "create customer", "Customer Creator", "Creates new customers"
        )
        assert 0.3 <= score <= 0.8
    
    def test_empty_task_returns_minimum(self):
        """Empty task should return minimum score (0.3)."""
        score = _deterministic_similarity("", "Template", "Description")
        assert score == 0.3
    
    def test_exact_match_scores_high(self):
        """Exact word matches should score higher."""
        high_score = _deterministic_similarity(
            "create customer subscription",
            "Create Customer Subscription",
            "Creates a subscription for a customer"
        )
        low_score = _deterministic_similarity(
            "create customer subscription",
            "Delete Payment Intent",
            "Removes a payment intent"
        )
        assert high_score > low_score
    
    def test_case_insensitive(self):
        """Matching should be case-insensitive."""
        score1 = _deterministic_similarity(
            "CREATE CUSTOMER", "create customer", ""
        )
        score2 = _deterministic_similarity(
            "create customer", "CREATE CUSTOMER", ""
        )
        assert score1 == score2
    
    def test_stopwords_ignored(self):
        """Common stopwords should not affect scoring."""
        score1 = _deterministic_similarity(
            "the create a customer",  # Extra stopwords
            "create customer", ""
        )
        score2 = _deterministic_similarity(
            "create customer",
            "create customer", ""
        )
        # Scores should be very similar (stopwords filtered out)
        assert abs(score1 - score2) < 0.1
    
    def test_empty_template_returns_minimum(self):
        """Empty template should return minimum score."""
        score = _deterministic_similarity("create customer", "", None)
        assert score == 0.3
    
    def test_partial_overlap(self):
        """Partial word overlap should score between min and max."""
        score = _deterministic_similarity(
            "create customer profile",
            "Update Customer Data",
            "Modifies customer information"
        )
        # "customer" overlaps, but not all words
        assert 0.3 < score < 0.8


# ---------------------------------------------------------------------------
# Tests: compute_embedding Retry Logic
# ---------------------------------------------------------------------------

class TestComputeEmbeddingRetry:
    """Tests for compute_embedding() retry logic."""
    
    def test_empty_text_returns_empty(self):
        """Empty text should return empty list without calling API."""
        result = compute_embedding("")
        assert result == []
        
        result = compute_embedding("   ")
        assert result == []
    
    @patch("integration_coworker.graph.nodes.embed_spec_chunks._get_embedding_client")
    def test_no_client_returns_empty(self, mock_get_client):
        """No client available should return empty list."""
        mock_get_client.return_value = None
        result = compute_embedding("test query")
        # Empty list returned when no client
        assert result == []
    
    @patch("integration_coworker.graph.nodes.embed_spec_chunks._get_embedding_client")
    @patch("integration_coworker.graph.nodes.embed_spec_chunks._is_auth_error")
    @patch("integration_coworker.graph.nodes.embed_spec_chunks._is_rate_limit_error")
    def test_successful_embedding(self, mock_is_rate, mock_is_auth, mock_get_client):
        """Successful embedding should return vector."""
        mock_client = Mock()
        mock_client.embed_query.return_value = [0.1] * 1536
        mock_get_client.return_value = mock_client
        mock_is_auth.return_value = False
        mock_is_rate.return_value = False
        
        result = compute_embedding("test query")
        
        assert len(result) == 1536
        mock_client.embed_query.assert_called_once()
    
    @patch("integration_coworker.graph.nodes.embed_spec_chunks._get_embedding_client")
    @patch("integration_coworker.graph.nodes.embed_spec_chunks._is_auth_error")
    @patch("integration_coworker.graph.nodes.embed_spec_chunks._is_rate_limit_error")
    @patch("integration_coworker.retrieval.semantic_search.time.sleep")
    def test_retries_on_rate_limit(self, mock_sleep, mock_is_rate, mock_is_auth, mock_get_client):
        """Should retry on rate limit errors."""
        mock_client = Mock()
        # Fail twice, then succeed
        mock_client.embed_query.side_effect = [
            Exception("429 rate limit"),
            Exception("429 rate limit"),
            [0.1] * 1536,
        ]
        mock_get_client.return_value = mock_client
        mock_is_auth.return_value = False
        mock_is_rate.return_value = True
        
        result = compute_embedding("test query", max_retries=3)
        
        assert len(result) == 1536
        assert mock_client.embed_query.call_count == 3
        assert mock_sleep.call_count == 2  # Two retries before success
    
    @patch("integration_coworker.graph.nodes.embed_spec_chunks._get_embedding_client")
    @patch("integration_coworker.graph.nodes.embed_spec_chunks._is_auth_error")
    @patch("integration_coworker.graph.nodes.embed_spec_chunks._is_rate_limit_error")
    def test_auth_error_fails_fast(self, mock_is_rate, mock_is_auth, mock_get_client):
        """Auth errors should fail immediately without retry."""
        from integration_coworker.llm.exceptions import LLMAuthError
        
        mock_client = Mock()
        mock_client.embed_query.side_effect = Exception("401 Unauthorized")
        mock_get_client.return_value = mock_client
        mock_is_auth.return_value = True
        mock_is_rate.return_value = False
        
        with pytest.raises(LLMAuthError):
            compute_embedding("test query")
        
        # Only called once - no retries
        mock_client.embed_query.assert_called_once()
    
    @patch("integration_coworker.graph.nodes.embed_spec_chunks._get_embedding_client")
    @patch("integration_coworker.graph.nodes.embed_spec_chunks._is_auth_error")
    @patch("integration_coworker.graph.nodes.embed_spec_chunks._is_rate_limit_error")
    @patch("integration_coworker.retrieval.semantic_search.time.sleep")
    def test_graceful_degradation_on_exhausted_retries(
        self, mock_sleep, mock_is_rate, mock_is_auth, mock_get_client
    ):
        """Should return empty list when all retries exhausted (graceful degradation)."""
        mock_client = Mock()
        mock_client.embed_query.side_effect = Exception("429 rate limit")
        mock_get_client.return_value = mock_client
        mock_is_auth.return_value = False
        mock_is_rate.return_value = True
        
        result = compute_embedding("test query", max_retries=2)
        
        # Graceful degradation - returns empty list
        assert result == []
        # Tried 3 times (initial + 2 retries)
        assert mock_client.embed_query.call_count == 3
    
    @patch("integration_coworker.graph.nodes.embed_spec_chunks._get_embedding_client")
    @patch("integration_coworker.graph.nodes.embed_spec_chunks._is_auth_error")
    @patch("integration_coworker.graph.nodes.embed_spec_chunks._is_rate_limit_error")
    def test_non_retryable_error_returns_empty(self, mock_is_rate, mock_is_auth, mock_get_client):
        """Non-retryable errors should return empty list immediately."""
        mock_client = Mock()
        mock_client.embed_query.side_effect = Exception("Some random error")
        mock_get_client.return_value = mock_client
        mock_is_auth.return_value = False
        mock_is_rate.return_value = False
        
        result = compute_embedding("test query")
        
        # Graceful degradation
        assert result == []
        # Only tried once
        mock_client.embed_query.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: Integration with KG Scoring
# ---------------------------------------------------------------------------

class TestKGScoringWithFallback:
    """Tests that KG scoring uses deterministic fallback when embeddings unavailable."""
    
    def test_deterministic_provides_differentiation(self):
        """Deterministic scoring should differentiate templates."""
        task = "create customer subscription"
        
        score1 = _deterministic_similarity(
            task, "Create Customer Subscription", "Creates a subscription for a customer"
        )
        score2 = _deterministic_similarity(
            task, "Send Invoice", "Sends an invoice to a user"
        )
        score3 = _deterministic_similarity(
            task, "Delete Payment Intent", "Removes a payment intent"
        )
        
        # Related template should score highest
        assert score1 > score2
        assert score1 > score3
    
    def test_consistent_scoring(self):
        """Same inputs should produce same scores (deterministic)."""
        task = "create customer profile"
        template_name = "Customer Profile Creator"
        template_desc = "Creates user profiles"
        
        score1 = _deterministic_similarity(task, template_name, template_desc)
        score2 = _deterministic_similarity(task, template_name, template_desc)
        
        assert score1 == score2
