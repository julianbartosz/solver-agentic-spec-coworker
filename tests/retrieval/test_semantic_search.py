"""
Tests for semantic search module.

Tests cosine similarity, spec chunk search, and KG template search.
"""
import json
import pytest
from unittest.mock import patch, MagicMock

from integration_coworker.retrieval.semantic_search import (
    cosine_similarity,
    search_spec_chunks,
    search_kg_templates,
    compute_embedding,
    _keyword_similarity,
    ChunkMatch,
    TemplateMatch,
)


class TestCosineSimilarity:
    """Unit tests for cosine_similarity function."""
    
    def test_identical_vectors(self):
        """Identical vectors have similarity 1.0."""
        v = [1.0, 0.0, 0.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0)
    
    def test_orthogonal_vectors(self):
        """Orthogonal vectors have similarity 0.0."""
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)
    
    def test_opposite_vectors(self):
        """Opposite vectors have similarity -1.0."""
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(-1.0)
    
    def test_similar_vectors(self):
        """Similar vectors have high positive similarity."""
        a = [1.0, 0.5, 0.2]
        b = [0.9, 0.6, 0.3]
        sim = cosine_similarity(a, b)
        assert sim > 0.9  # Should be very similar
    
    def test_empty_vectors_return_zero(self):
        """Empty vectors return 0.0."""
        assert cosine_similarity([], []) == 0.0
        assert cosine_similarity([1.0], []) == 0.0
        assert cosine_similarity([], [1.0]) == 0.0
    
    def test_different_length_vectors_return_zero(self):
        """Vectors of different lengths return 0.0."""
        a = [1.0, 0.0]
        b = [1.0, 0.0, 0.0]
        assert cosine_similarity(a, b) == 0.0
    
    def test_zero_vectors_return_zero(self):
        """Zero vectors return 0.0 (avoid division by zero)."""
        a = [0.0, 0.0]
        b = [1.0, 1.0]
        assert cosine_similarity(a, b) == 0.0
        assert cosine_similarity(b, a) == 0.0
        assert cosine_similarity(a, a) == 0.0


class TestKeywordSimilarity:
    """Unit tests for _keyword_similarity fallback function."""
    
    def test_exact_match(self):
        """Exact word matches produce positive similarity."""
        sim = _keyword_similarity("create payment", "Create Payment", None)
        assert sim > 0.5
    
    def test_partial_match(self):
        """Partial word overlap produces non-zero similarity."""
        sim = _keyword_similarity("create checkout session", "checkout", "Start a checkout")
        assert sim > 0
    
    def test_no_match(self):
        """No word overlap produces zero similarity."""
        sim = _keyword_similarity("create payment", "delete subscription", None)
        # Still 0 if no overlap
        assert sim == 0.0
    
    def test_empty_query(self):
        """Empty query returns 0."""
        assert _keyword_similarity("", "some name", None) == 0.0
    
    def test_empty_name(self):
        """Empty name and description returns 0."""
        sim = _keyword_similarity("create payment", "", None)
        assert sim == 0.0


class TestComputeEmbedding:
    """Tests for compute_embedding function."""
    
    def test_empty_text_returns_empty(self):
        """Empty or whitespace-only text returns empty list."""
        assert compute_embedding("") == []
        assert compute_embedding("   ") == []
    
    def test_valid_text_returns_list(self):
        """Valid text returns a list (may be empty if no client available)."""
        # This test verifies the function handles valid input gracefully
        # In practice, result depends on whether real client is available
        result = compute_embedding("test query")
        assert isinstance(result, list)


class TestSearchSpecChunks:
    """Tests for search_spec_chunks function."""
    
    @patch('integration_coworker.retrieval.semantic_search.compute_embedding')
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_returns_top_k_results(self, mock_db, mock_embed):
        """Returns top_k results sorted by similarity."""
        # Setup mock embedding
        mock_embed.return_value = [1.0, 0.0, 0.0]
        
        # Setup mock DB
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        # Create test rows with embeddings
        mock_cursor.fetchall.return_value = [
            (1, 1, 0, "High similarity content", json.dumps([0.9, 0.1, 0.0])),
            (2, 1, 1, "Medium similarity content", json.dumps([0.5, 0.5, 0.0])),
            (3, 1, 2, "Low similarity content", json.dumps([0.0, 0.0, 1.0])),
        ]
        
        results = search_spec_chunks("test query", top_k=2)
        
        assert len(results) == 2
        assert isinstance(results[0], ChunkMatch)
        # First result should have highest similarity
        assert results[0].similarity > results[1].similarity
    
    @patch('integration_coworker.retrieval.semantic_search.compute_embedding')
    def test_no_embedding_returns_empty(self, mock_embed):
        """When compute_embedding returns empty, search returns empty."""
        mock_embed.return_value = []
        results = search_spec_chunks("test query")
        assert results == []
    
    @patch('integration_coworker.retrieval.semantic_search.compute_embedding')
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_filters_by_spec_document_id(self, mock_db, mock_embed):
        """Filters results by spec_document_id when provided."""
        mock_embed.return_value = [1.0, 0.0, 0.0]
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []
        
        search_spec_chunks("test", spec_document_id=42)
        
        # Verify the SQL was called with the document ID filter
        call_args = mock_cursor.execute.call_args
        assert "spec_document_id = ?" in call_args[0][0]
        assert 42 in call_args[0][1]


class TestSearchKgTemplates:
    """Tests for search_kg_templates function."""
    
    @patch('integration_coworker.retrieval.semantic_search.compute_embedding')
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_returns_template_matches(self, mock_db, mock_embed):
        """Returns TemplateMatch objects with combined scores."""
        mock_embed.return_value = [1.0, 0.0, 0.0]
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        mock_cursor.fetchall.return_value = [
            (1, "template_1", "Create Payment", "Standard payment flow", json.dumps([0.9, 0.1, 0.0])),
            (2, "template_2", "Get Customer", "Retrieve customer info", json.dumps([0.3, 0.7, 0.0])),
        ]
        
        results = search_kg_templates("create payment")
        
        assert len(results) == 2
        assert isinstance(results[0], TemplateMatch)
        assert results[0].combined_score >= results[1].combined_score
    
    @patch('integration_coworker.retrieval.semantic_search.compute_embedding')
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_filters_by_provider(self, mock_db, mock_embed):
        """Filters by provider_code when provided."""
        mock_embed.return_value = [1.0, 0.0, 0.0]
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []
        
        search_kg_templates("test", provider_code="stripe")
        
        # Verify the SQL includes provider filter
        call_args = mock_cursor.execute.call_args
        assert "provider_code = ?" in call_args[0][0]
    
    @patch('integration_coworker.retrieval.semantic_search.compute_embedding')
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_fallback_to_keyword_when_no_embedding(self, mock_db, mock_embed):
        """Falls back to keyword similarity when embeddings unavailable."""
        mock_embed.return_value = []  # No embedding available
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        # Return template with matching keywords but no embedding
        mock_cursor.fetchall.return_value = [
            (1, "create_payment", "Create Payment", "Create a new payment", None),
        ]
        
        results = search_kg_templates("create payment")
        
        # Should still return results using keyword matching
        assert len(results) == 1
        assert results[0].similarity > 0  # Keyword match should produce positive score


class TestIntegration:
    """Integration tests with actual database (uses test fixtures)."""
    
    def test_search_spec_chunks_with_db(self):
        """
        Integration test: verify search_spec_chunks works with real DB.
        
        Note: This test may return empty results if no chunks with embeddings
        exist in the test database. That's expected behavior.
        """
        # This will use the real DB connection
        # Returns empty if no embeddings or if compute_embedding fails
        results = search_spec_chunks("create payment", top_k=3)
        assert isinstance(results, list)
        # Each result should be a ChunkMatch if any results exist
        for r in results:
            assert isinstance(r, ChunkMatch)
    
    def test_search_kg_templates_with_db(self):
        """
        Integration test: verify search_kg_templates works with real DB.
        
        Note: This test may return empty results if no KG templates exist.
        """
        results = search_kg_templates("create checkout session", top_k=3)
        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, TemplateMatch)
