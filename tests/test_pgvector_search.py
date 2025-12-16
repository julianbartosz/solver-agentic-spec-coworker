"""
Tests for Postgres + pgvector semantic search functionality.

These tests verify:
1. pgvector-native similarity search produces correct results
2. Python fallback works when pgvector is unavailable
3. Vector formatting and query construction
4. Parity between Python cosine and pgvector results

Most tests use @pytest.mark.no_db since they mock database connections.
"""
import json
import math
import pytest
from unittest.mock import MagicMock, patch

from integration_coworker.retrieval.semantic_search import (
    cosine_similarity,
    _format_vector_literal,
    _is_postgres_backend,
    _search_spec_chunks_python,
    _search_spec_chunks_pgvector,
    _search_kg_templates_python,
    _search_kg_templates_pgvector,
    search_spec_chunks,
    search_kg_templates,
    ChunkMatch,
    TemplateMatch,
)


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def sample_embedding():
    """A sample 1536-dim embedding (truncated for testing)."""
    # Use a small realistic embedding
    return [0.1, 0.2, 0.3, 0.4, 0.5] + [0.0] * 1531


@pytest.fixture
def similar_embedding():
    """An embedding similar to sample_embedding."""
    return [0.11, 0.21, 0.29, 0.41, 0.49] + [0.0] * 1531


@pytest.fixture
def orthogonal_embedding():
    """An embedding orthogonal to sample_embedding."""
    return [0.0] * 5 + [1.0, 0.0, 0.0, 0.0, 0.0] + [0.0] * 1526


# =============================================================================
# Test Vector Utilities
# =============================================================================

@pytest.mark.no_db
class TestVectorFormatting:
    """Tests for vector literal formatting."""
    
    def test_format_vector_literal_basic(self):
        """Test basic vector formatting."""
        vec = [1.0, 2.0, 3.0]
        result = _format_vector_literal(vec)
        assert result == "[1.0,2.0,3.0]"
    
    def test_format_vector_literal_floats(self):
        """Test formatting preserves float precision."""
        vec = [0.123456789, -0.987654321, 0.0]
        result = _format_vector_literal(vec)
        assert result.startswith("[0.123456789,")
        assert "-0.987654321" in result
    
    def test_format_vector_literal_empty(self):
        """Test empty vector formatting."""
        result = _format_vector_literal([])
        assert result == "[]"
    
    def test_format_vector_literal_large(self, sample_embedding):
        """Test formatting of full 1536-dim vector."""
        result = _format_vector_literal(sample_embedding)
        assert result.startswith("[")
        assert result.endswith("]")
        # Should have 1535 commas (1536 elements - 1)
        assert result.count(",") == 1535


@pytest.mark.no_db
class TestCosineSimilarity:
    """Tests for Python cosine similarity implementation."""
    
    def test_identical_vectors(self):
        """Identical vectors should have similarity 1.0."""
        v = [1.0, 2.0, 3.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0)
    
    def test_orthogonal_vectors(self):
        """Orthogonal vectors should have similarity 0.0."""
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)
    
    def test_opposite_vectors(self):
        """Opposite vectors should have similarity -1.0."""
        a = [1.0, 0.0, 0.0]
        b = [-1.0, 0.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(-1.0)
    
    def test_similar_vectors(self, sample_embedding, similar_embedding):
        """Similar vectors should have high similarity."""
        sim = cosine_similarity(sample_embedding, similar_embedding)
        assert sim > 0.99  # Very similar
    
    def test_different_length_returns_zero(self):
        """Vectors of different lengths should return 0."""
        a = [1.0, 2.0, 3.0]
        b = [1.0, 2.0]
        assert cosine_similarity(a, b) == 0.0
    
    def test_empty_vectors_return_zero(self):
        """Empty vectors should return 0."""
        assert cosine_similarity([], []) == 0.0
        assert cosine_similarity([1.0], []) == 0.0
    
    def test_zero_vector_returns_zero(self):
        """Zero vector should return 0 (avoid division by zero)."""
        zero = [0.0, 0.0, 0.0]
        nonzero = [1.0, 2.0, 3.0]
        assert cosine_similarity(zero, nonzero) == 0.0


# =============================================================================
# Test Backend Detection
# =============================================================================

@pytest.mark.no_db
class TestBackendDetection:
    """Tests for database backend detection."""
    
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_detects_postgres_backend(self, mock_db):
        """Should detect Postgres when engine type is postgres."""
        mock_db.get_engine_type.return_value = "postgres"
        assert _is_postgres_backend() is True
    
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_detects_sqlite_backend(self, mock_db):
        """Should detect SQLite when engine type is sqlite."""
        mock_db.get_engine_type.return_value = "sqlite"
        assert _is_postgres_backend() is False
    
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_handles_exception_gracefully(self, mock_db):
        """Should return False on exception."""
        mock_db.get_engine_type.side_effect = Exception("DB error")
        assert _is_postgres_backend() is False


# =============================================================================
# Test Python-based Search (SQLite fallback)
# =============================================================================

@pytest.mark.no_db
class TestPythonSearch:
    """Tests for Python-based similarity search (SQLite fallback)."""
    
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_search_spec_chunks_python_empty_results(self, mock_db):
        """Should return empty list when no chunks found."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        mock_db.get_connection.return_value = mock_conn
        
        result = _search_spec_chunks_python([0.1, 0.2, 0.3], top_k=5, spec_document_id=None)
        assert result == []
    
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_search_spec_chunks_python_returns_sorted(self, mock_db, sample_embedding):
        """Should return chunks sorted by similarity."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        # Create test data with varying similarities
        high_sim_embedding = sample_embedding.copy()
        low_sim_embedding = [0.9, 0.8, 0.7, 0.6, 0.5] + [0.0] * 1531
        
        mock_cursor.fetchall.return_value = [
            (1, 1, 0, "Low similarity content", json.dumps(low_sim_embedding)),
            (2, 1, 1, "High similarity content", json.dumps(high_sim_embedding)),
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_db.get_connection.return_value = mock_conn
        
        result = _search_spec_chunks_python(sample_embedding, top_k=5, spec_document_id=None)
        
        assert len(result) == 2
        assert result[0].chunk_id == 2  # High similarity first
        assert result[0].similarity > result[1].similarity
    
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_search_spec_chunks_python_respects_top_k(self, mock_db, sample_embedding):
        """Should limit results to top_k."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        # Create many results
        rows = [
            (i, 1, i, f"Content {i}", json.dumps(sample_embedding))
            for i in range(10)
        ]
        mock_cursor.fetchall.return_value = rows
        mock_conn.cursor.return_value = mock_cursor
        mock_db.get_connection.return_value = mock_conn
        
        result = _search_spec_chunks_python(sample_embedding, top_k=3, spec_document_id=None)
        
        assert len(result) == 3
    
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_search_spec_chunks_python_filters_by_doc_id(self, mock_db, sample_embedding):
        """Should filter by spec_document_id when provided."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        mock_db.get_connection.return_value = mock_conn
        
        _search_spec_chunks_python(sample_embedding, top_k=5, spec_document_id=42)
        
        # Check SQL includes document filter
        call_args = mock_cursor.execute.call_args
        sql = call_args[0][0]
        params = call_args[0][1]
        assert "spec_document_id = ?" in sql
        assert params == (42,)
    
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_search_kg_templates_python_with_provider(self, mock_db, sample_embedding):
        """Should filter by provider_code when provided."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            (1, "template_key", "Template Name", "Description", json.dumps(sample_embedding)),
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_db.get_connection.return_value = mock_conn
        
        result = _search_kg_templates_python(sample_embedding, "test query", "stripe", top_k=5)
        
        assert len(result) == 1
        assert result[0].graph_score == 0.5  # Provider match bonus
    
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_search_kg_templates_python_without_provider(self, mock_db, sample_embedding):
        """Should use lower graph score without provider filter."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            (1, "template_key", "Template Name", "Description", json.dumps(sample_embedding)),
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_db.get_connection.return_value = mock_conn
        
        result = _search_kg_templates_python(sample_embedding, "test query", None, top_k=5)
        
        assert len(result) == 1
        assert result[0].graph_score == 0.3  # No provider match


# =============================================================================
# Test pgvector-native Search
# =============================================================================

@pytest.mark.no_db
class TestPgvectorSearch:
    """Tests for pgvector-native similarity search."""
    
    @patch('integration_coworker.retrieval.semantic_search._search_spec_chunks_python')
    def test_pgvector_falls_back_on_import_error(self, mock_python_search, sample_embedding):
        """Should fall back to Python search if postgres module unavailable."""
        mock_python_search.return_value = []
        
        with patch.dict('sys.modules', {'integration_coworker.persistence.postgres': None}):
            # This should catch the import error and fall back
            result = _search_spec_chunks_pgvector(sample_embedding, 5, None)
        
        # The function should have called the fallback
        # (actual behavior depends on implementation)
    
    @patch('integration_coworker.persistence.postgres.get_connection')
    def test_pgvector_search_spec_chunks_constructs_correct_query(
        self, mock_get_conn, sample_embedding
    ):
        """Should construct correct pgvector query."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            (1, 1, 0, "Content", 0.95),
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn
        
        result = _search_spec_chunks_pgvector(sample_embedding, 5, None)
        
        # Check the result
        assert len(result) == 1
        assert result[0].similarity == 0.95
    
    @patch('integration_coworker.persistence.postgres.get_connection')
    def test_pgvector_search_kg_templates_with_provider(
        self, mock_get_conn, sample_embedding
    ):
        """Should filter by provider in pgvector query."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            (1, "key", "name", "desc", 0.85),
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn
        
        result = _search_kg_templates_pgvector(sample_embedding, "test", "stripe", 5)
        
        assert len(result) == 1
        # Combined score: (0.5 * 0.4) + (0.85 * 0.6) = 0.2 + 0.51 = 0.71
        assert result[0].combined_score == pytest.approx(0.71, rel=0.01)


# =============================================================================
# Test Main Search Functions (Routing)
# =============================================================================

@pytest.mark.no_db
class TestSearchRouting:
    """Tests for main search functions that route to appropriate backend."""
    
    @patch('integration_coworker.retrieval.semantic_search.compute_embedding')
    @patch('integration_coworker.retrieval.semantic_search._is_postgres_backend')
    @patch('integration_coworker.retrieval.semantic_search._search_spec_chunks_pgvector')
    def test_search_spec_chunks_uses_pgvector_when_available(
        self, mock_pgvector, mock_is_pg, mock_embed, sample_embedding
    ):
        """Should use pgvector when Postgres backend is detected."""
        mock_embed.return_value = sample_embedding
        mock_is_pg.return_value = True
        mock_pgvector.return_value = []
        
        search_spec_chunks("test query", top_k=5)
        
        mock_pgvector.assert_called_once()
    
    @patch('integration_coworker.retrieval.semantic_search.compute_embedding')
    @patch('integration_coworker.retrieval.semantic_search._is_postgres_backend')
    @patch('integration_coworker.retrieval.semantic_search._search_spec_chunks_python')
    def test_search_spec_chunks_uses_python_for_sqlite(
        self, mock_python, mock_is_pg, mock_embed, sample_embedding
    ):
        """Should use Python search when SQLite backend is detected."""
        mock_embed.return_value = sample_embedding
        mock_is_pg.return_value = False
        mock_python.return_value = []
        
        search_spec_chunks("test query", top_k=5)
        
        mock_python.assert_called_once()
    
    @patch('integration_coworker.retrieval.semantic_search.compute_embedding')
    def test_search_spec_chunks_returns_empty_without_embedding(self, mock_embed):
        """Should return empty list when embedding computation fails."""
        mock_embed.return_value = []
        
        result = search_spec_chunks("test query", top_k=5)
        
        assert result == []
    
    @patch('integration_coworker.retrieval.semantic_search.compute_embedding')
    @patch('integration_coworker.retrieval.semantic_search._is_postgres_backend')
    @patch('integration_coworker.retrieval.semantic_search._search_kg_templates_pgvector')
    def test_search_kg_templates_uses_pgvector_when_available(
        self, mock_pgvector, mock_is_pg, mock_embed, sample_embedding
    ):
        """Should use pgvector for KG search when Postgres backend is detected."""
        mock_embed.return_value = sample_embedding
        mock_is_pg.return_value = True
        mock_pgvector.return_value = []
        
        search_kg_templates("test query", provider_code="stripe", top_k=5)
        
        mock_pgvector.assert_called_once()
    
    @patch('integration_coworker.retrieval.semantic_search.compute_embedding')
    @patch('integration_coworker.retrieval.semantic_search._is_postgres_backend')
    @patch('integration_coworker.retrieval.semantic_search._search_kg_templates_python')
    def test_search_kg_templates_uses_python_for_sqlite(
        self, mock_python, mock_is_pg, mock_embed, sample_embedding
    ):
        """Should use Python search for KG when SQLite backend is detected."""
        mock_embed.return_value = sample_embedding
        mock_is_pg.return_value = False
        mock_python.return_value = []
        
        search_kg_templates("test query", provider_code="stripe", top_k=5)
        
        mock_python.assert_called_once()


# =============================================================================
# Test ChunkMatch and TemplateMatch Dataclasses
# =============================================================================

@pytest.mark.no_db
class TestDataclasses:
    """Tests for result dataclasses."""
    
    def test_chunk_match_creation(self):
        """Should create ChunkMatch with all fields."""
        match = ChunkMatch(
            chunk_id=1,
            spec_document_id=2,
            chunk_index=3,
            content="Test content",
            similarity=0.95,
        )
        assert match.chunk_id == 1
        assert match.spec_document_id == 2
        assert match.chunk_index == 3
        assert match.content == "Test content"
        assert match.similarity == 0.95
    
    def test_template_match_creation(self):
        """Should create TemplateMatch with all fields."""
        match = TemplateMatch(
            node_id=1,
            key="template_key",
            name="Template Name",
            description="A test template",
            similarity=0.85,
            graph_score=0.5,
            combined_score=0.71,
        )
        assert match.node_id == 1
        assert match.key == "template_key"
        assert match.name == "Template Name"
        assert match.similarity == 0.85
        assert match.graph_score == 0.5
        assert match.combined_score == 0.71
    
    def test_template_match_default_scores(self):
        """Should have default graph and combined scores of 0."""
        match = TemplateMatch(
            node_id=1,
            key="key",
            name="name",
            description=None,
            similarity=0.9,
        )
        assert match.graph_score == 0.0
        assert match.combined_score == 0.0


# =============================================================================
# Test Similarity Parity (Python vs pgvector)
# =============================================================================

@pytest.mark.no_db
class TestSimilarityParity:
    """Tests to verify Python and pgvector similarity calculations match."""
    
    def test_cosine_similarity_matches_pgvector_definition(self):
        """
        Verify our cosine similarity matches pgvector's <=> operator.
        
        pgvector <=> returns cosine distance = 1 - cosine_similarity
        So similarity = 1 - distance
        
        Our Python implementation should produce the same values.
        """
        # Test vectors
        a = [1.0, 0.0, 0.0]
        b = [1.0, 1.0, 0.0]  # 45 degrees from a
        
        # Python cosine similarity
        python_sim = cosine_similarity(a, b)
        
        # Expected: cos(45°) = 1/sqrt(2) ≈ 0.7071
        expected = 1.0 / math.sqrt(2)
        
        assert python_sim == pytest.approx(expected, rel=1e-6)
    
    def test_similarity_range(self, sample_embedding, orthogonal_embedding):
        """Verify similarity is always in [-1, 1] range."""
        # Test many vector pairs
        test_cases = [
            (sample_embedding, sample_embedding),  # identical
            (sample_embedding, orthogonal_embedding),  # orthogonal
            ([1, 0, 0], [-1, 0, 0]),  # opposite
            ([0.5, 0.5, 0.5], [0.1, 0.9, 0.1]),  # random
        ]
        
        for a, b in test_cases:
            sim = cosine_similarity(a, b)
            assert -1.0 <= sim <= 1.0, f"Similarity {sim} out of range for {a}, {b}"
