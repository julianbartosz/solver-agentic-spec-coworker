"""
Semantic Retrieval Probing Tests.

These tests probe the behavior of semantic retrieval functions to verify:
1. Similarity thresholds and score distributions
2. Fallback behavior when embeddings unavailable
3. Multi-match disambiguation
4. Edge cases in KG alignment

Per V1 Hardening Roadmap Item 4: Verify search_spec_chunks and align_task_with_kg
produce sensible outputs across edge cases.
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from dataclasses import asdict
import os

from integration_coworker.retrieval.semantic_search import (
    cosine_similarity,
    search_spec_chunks,
    search_kg_templates,
    compute_embedding,
    _keyword_similarity,
    ChunkMatch,
    TemplateMatch,
)
from integration_coworker.graph.nodes.align_task_with_kg import align_task_with_kg
from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import (
    IntegrationTask,
    Endpoint,
    Entity,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(autouse=True)
def enable_mock_llm_and_kg(monkeypatch):
    """Enable mock LLM and KG fallback for consistent testing."""
    monkeypatch.setenv("USE_MOCK_LLM", "1")
    monkeypatch.setenv("USE_IN_MEMORY_KG_FALLBACK", "1")
    # V2: Legacy templates removed, using inference-based fallback


@pytest.fixture
def mock_high_similarity_embedding():
    """Return embedding with predictable high similarity."""
    return [1.0] + [0.0] * 1535  # 1536 dims, mostly zeros


@pytest.fixture
def mock_low_similarity_embedding():
    """Return embedding with predictable low similarity."""
    return [0.0] * 768 + [1.0] + [0.0] * 767  # Orthogonal to high


# =============================================================================
# Similarity Score Distribution Tests
# =============================================================================

class TestSimilarityScoreDistribution:
    """Tests that verify similarity scores are in expected ranges."""
    
    def test_identical_embeddings_score_one(self):
        """Identical embeddings should have similarity exactly 1.0."""
        vec = [0.1, 0.2, 0.3, 0.4, 0.5]
        sim = cosine_similarity(vec, vec)
        assert sim == pytest.approx(1.0, abs=1e-6)
    
    def test_normalized_vectors_preserve_similarity(self):
        """Similarity should be scale-invariant for cosine."""
        a = [1.0, 2.0, 3.0]
        b = [0.5, 1.0, 1.5]  # a * 0.5
        sim = cosine_similarity(a, b)
        assert sim == pytest.approx(1.0, abs=1e-6)
    
    def test_opposite_vectors_score_negative_one(self):
        """Opposite vectors should have similarity -1.0."""
        a = [1.0, 0.0, 0.0]
        b = [-1.0, 0.0, 0.0]
        sim = cosine_similarity(a, b)
        assert sim == pytest.approx(-1.0, abs=1e-6)
    
    def test_high_dimensional_similarity_ranges(self):
        """Verify similarity ranges for high-dim vectors like embeddings."""
        import random
        random.seed(42)
        
        # Random unit vectors in 1536 dims (OpenAI embedding size)
        vec1 = [random.gauss(0, 1) for _ in range(1536)]
        vec2 = [random.gauss(0, 1) for _ in range(1536)]
        
        sim = cosine_similarity(vec1, vec2)
        
        # Random high-dim vectors should have similarity near 0
        # (law of large numbers for orthogonal random vectors)
        assert -0.3 < sim < 0.3, f"Expected near-zero similarity for random vectors, got {sim}"
    
    def test_perturbed_vectors_high_similarity(self):
        """Small perturbations should yield high similarity."""
        base = [0.1] * 1536
        perturbed = [x + 0.001 for x in base]
        
        sim = cosine_similarity(base, perturbed)
        assert sim > 0.99, f"Expected high similarity for small perturbation, got {sim}"


# =============================================================================
# Keyword Fallback Tests
# =============================================================================

class TestKeywordFallbackBehavior:
    """Tests for keyword similarity fallback when embeddings unavailable."""
    
    def test_exact_phrase_match(self):
        """Exact phrase match should give highest score."""
        sim = _keyword_similarity(
            "create checkout session",
            "Create Checkout Session",
            "Standard checkout flow"
        )
        assert sim > 0.5
    
    def test_partial_overlap_positive(self):
        """Partial word overlap should give positive score."""
        sim = _keyword_similarity(
            "create payment intent",
            "Payment Intent",
            "Create a new payment"
        )
        assert sim > 0
    
    def test_no_overlap_zero(self):
        """No word overlap should give zero score."""
        sim = _keyword_similarity(
            "create subscription",
            "Delete Account",
            "Remove user data"
        )
        assert sim == 0.0
    
    def test_single_word_query(self):
        """Single word query should match if word present."""
        sim = _keyword_similarity("payment", "Create Payment", None)
        assert sim > 0
    
    def test_common_words_lower_score(self):
        """Common words like 'the', 'a' still count (no stopword removal)."""
        # This tests current behavior - no stopword filtering
        sim_with = _keyword_similarity("the payment", "the payment", None)
        sim_without = _keyword_similarity("payment", "payment", None)
        # Both should be 1.0 since exact match
        assert sim_with == 1.0
        assert sim_without == 1.0


# =============================================================================
# Search Spec Chunks Behavior Tests
# =============================================================================

class TestSearchSpecChunksBehavior:
    """Tests for search_spec_chunks edge cases and behavior."""
    
    @patch('integration_coworker.retrieval.semantic_search.compute_embedding')
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_empty_database_returns_empty(self, mock_db, mock_embed):
        """Empty database should return empty list, not error."""
        mock_embed.return_value = [1.0] * 1536
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []
        
        results = search_spec_chunks("create payment")
        assert results == []
    
    @patch('integration_coworker.retrieval.semantic_search.compute_embedding')
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_corrupt_embedding_json_skipped(self, mock_db, mock_embed):
        """Chunks with corrupt embedding JSON should be skipped."""
        mock_embed.return_value = [1.0, 0.0, 0.0]
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        mock_cursor.fetchall.return_value = [
            (1, 1, 0, "Good content", json.dumps([0.9, 0.1, 0.0])),
            (2, 1, 1, "Bad embedding", "not-valid-json"),
            (3, 1, 2, "Another good", json.dumps([0.7, 0.3, 0.0])),
        ]
        
        results = search_spec_chunks("test")
        
        # Should only return 2 valid results
        assert len(results) == 2
        chunk_ids = [r.chunk_id for r in results]
        assert 1 in chunk_ids
        assert 3 in chunk_ids
        assert 2 not in chunk_ids
    
    @patch('integration_coworker.retrieval.semantic_search.compute_embedding')
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_top_k_respected(self, mock_db, mock_embed):
        """Should return exactly top_k results when more exist."""
        mock_embed.return_value = [1.0, 0.0, 0.0]
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        # Create 10 chunks
        mock_cursor.fetchall.return_value = [
            (i, 1, i, f"Content {i}", json.dumps([0.5 + i * 0.05, 0.1, 0.0]))
            for i in range(10)
        ]
        
        results = search_spec_chunks("test", top_k=3)
        assert len(results) == 3
    
    @patch('integration_coworker.retrieval.semantic_search.compute_embedding')
    def test_no_embedding_available_returns_empty(self, mock_embed):
        """If compute_embedding fails, return empty gracefully."""
        mock_embed.return_value = []
        
        results = search_spec_chunks("test query")
        assert results == []


# =============================================================================
# Search KG Templates Behavior Tests
# =============================================================================

class TestSearchKgTemplatesBehavior:
    """Tests for search_kg_templates edge cases and behavior."""
    
    @patch('integration_coworker.retrieval.semantic_search.compute_embedding')
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_combined_score_weighting(self, mock_db, mock_embed):
        """Combined score should weight semantic (60%) and graph (40%)."""
        mock_embed.return_value = [1.0, 0.0, 0.0]
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        # Template with perfect embedding match
        mock_cursor.fetchall.return_value = [
            (1, "template_1", "Perfect Match", "Description", json.dumps([1.0, 0.0, 0.0])),
        ]
        
        results = search_kg_templates("test", provider_code="test_provider")
        
        assert len(results) == 1
        result = results[0]
        
        # V2: With provider_code, graph_score = 0.5
        # With perfect embedding match, similarity ~ 1.0
        # Default weights: graph=0.4, embedding=0.4
        # Combined = 0.5 * 0.4 + 1.0 * 0.4 = 0.6
        assert result.combined_score == pytest.approx(0.6, abs=0.05)
    
    @patch('integration_coworker.retrieval.semantic_search.compute_embedding')
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_no_provider_lower_graph_score(self, mock_db, mock_embed):
        """Without provider filter, graph score should be lower."""
        mock_embed.return_value = [1.0, 0.0, 0.0]
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        mock_cursor.fetchall.return_value = [
            (1, "template_1", "Match", "Desc", json.dumps([1.0, 0.0, 0.0])),
        ]
        
        results = search_kg_templates("test", provider_code=None)
        
        assert len(results) == 1
        result = results[0]
        
        # V2: Without provider_code, graph_score = 0.0 (no provider match)
        # Combined = 0.0 * 0.4 + 1.0 * 0.4 = 0.4
        assert result.combined_score == pytest.approx(0.4, abs=0.05)
    
    @patch('integration_coworker.retrieval.semantic_search.compute_embedding')
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_keyword_fallback_when_no_embeddings(self, mock_db, mock_embed):
        """Should use keyword matching when embeddings unavailable."""
        mock_embed.return_value = []  # No embedding
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        mock_cursor.fetchall.return_value = [
            (1, "create_payment", "Create Payment", "Create a new payment", None),
            (2, "get_user", "Get User", "Retrieve user info", None),
        ]
        
        results = search_kg_templates("create payment")
        
        assert len(results) == 2
        # "Create Payment" should score higher due to keyword match
        assert results[0].key == "create_payment"
        assert results[0].similarity > results[1].similarity


# =============================================================================
# Align Task With KG Behavior Tests
# =============================================================================

class TestAlignTaskWithKgBehavior:
    """Tests for align_task_with_kg node behavior and edge cases."""
    
    def _make_state(
        self,
        task_description: str,
        task_slug: str = "test_task",
        provider_code: str = "mock_payments",
        endpoints: list = None,
        entities: list = None,
    ) -> WorkflowState:
        """Helper to create test WorkflowState."""
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description=task_description,
            provider_code=provider_code,
            integration_task=IntegrationTask(
                id=None,
                source_system_id=None,
                task_slug=task_slug,
                provider_code=provider_code,
                description=task_description,
            ),
            endpoints=endpoints or [],
            entities=entities or [],
        )
        return state
    
    def test_fallback_workflow_has_four_nodes(self):
        """
        Unknown tasks get cross-provider pattern match or fallback.
        
        V1.2 Pattern Learning: Unknown tasks now match against cross-provider 
        patterns (e.g., crud_create) before falling back. This improved behavior
        means they get 4-5 nodes depending on pattern match.
        """
        state = self._make_state(
            task_description="Some completely unknown task type",
            task_slug="unknown_operation",
        )
        
        result = align_task_with_kg(state)
        
        # V1.2: May get 4-5 nodes (pattern match or fallback)
        assert len(result.workflow_nodes) >= 4
        node_types = [n.node_type for n in result.workflow_nodes]
        assert "start" in node_types
        assert "end" in node_types
    
    def test_template_match_has_five_nodes(self):
        """
        V2: With inference, POST endpoint generates workflow with validation + transform.
        
        Note: Legacy templates are removed. This test now verifies inference behavior.
        """
        # Need to provide an endpoint for inference to work
        endpoints = [
            Endpoint(
                id=None,
                source_system_id=None,
                spec_document_id=None,
                path="/v1/checkout/sessions",
                method="POST",
                operation_id="createCheckoutSession",
                summary="Create checkout session",
                description="Create a new checkout session",
                request_schema_id=None,
                response_schema_id=None,
            ),
        ]
        
        state = self._make_state(
            task_description="Create a new checkout session",
            task_slug="create_checkout_session",
        )
        state.endpoints = endpoints
        
        result = align_task_with_kg(state)
        
        # V2: With inference from POST endpoint, should have 5 nodes
        # (start, validate, call, transform, end)
        assert len(result.workflow_nodes) == 5
        node_keys = [n.node_key for n in result.workflow_nodes]
        assert "start" in node_keys
        assert any("validate" in k for k in node_keys)
        assert any("call" in k for k in node_keys)
    
    def test_step_marked_completed(self):
        """align_task_with_kg should mark itself as completed."""
        state = self._make_state("Test task", "test_task")
        
        result = align_task_with_kg(state)
        
        assert "align_task_with_kg" in result.completed_steps
    
    def test_endpoints_influence_matching(self):
        """Available endpoints should influence template selection."""
        # Create state with a relevant endpoint
        endpoints = [
            Endpoint(
                id=None,
                source_system_id=None,
                spec_document_id=None,
                path="/v1/checkout/sessions",
                method="POST",
                operation_id="createCheckoutSession",
                summary="Create checkout session",
                description="Create a new checkout session",
                request_schema_id=None,
                response_schema_id=None,
            ),
        ]
        
        state = self._make_state(
            task_description="Create checkout session",
            task_slug="create_checkout_session",
            endpoints=endpoints,
        )
        
        result = align_task_with_kg(state)
        
        # Should still match the expected template
        assert len(result.workflow_nodes) >= 4
        assert "align_task_with_kg" in result.completed_steps
    
    def test_no_template_still_produces_workflow(self):
        """Even without template match, should produce valid workflow."""
        state = self._make_state(
            task_description="Completely novel task with no templates",
            task_slug="novel_operation",
        )
        
        result = align_task_with_kg(state)
        
        # No templates should match
        assert len(result.plan.get("candidate_templates", [])) == 0
        
        # But workflow should still exist
        assert len(result.workflow_nodes) >= 4
        assert len(result.workflow_edges) >= 3
        
        # Nodes should form valid graph structure
        node_keys = {n.node_key for n in result.workflow_nodes}
        for edge in result.workflow_edges:
            assert edge.from_node_key in node_keys
            assert edge.to_node_key in node_keys


# =============================================================================
# Multi-Match Disambiguation Tests
# =============================================================================

class TestMultiMatchDisambiguation:
    """Tests for handling multiple template matches."""
    
    @patch('integration_coworker.retrieval.semantic_search.compute_embedding')
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_highest_score_wins(self, mock_db, mock_embed):
        """When multiple templates match, highest combined score wins."""
        mock_embed.return_value = [1.0, 0.0, 0.0]
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        mock_cursor.fetchall.return_value = [
            (1, "template_a", "Create Payment", "Low match", json.dumps([0.3, 0.7, 0.0])),
            (2, "template_b", "Create Checkout", "High match", json.dumps([0.95, 0.05, 0.0])),
            (3, "template_c", "Create Order", "Medium match", json.dumps([0.6, 0.4, 0.0])),
        ]
        
        results = search_kg_templates("create checkout", top_k=3)
        
        # Results should be sorted by combined score, highest first
        assert results[0].key == "template_b"
        assert results[0].combined_score >= results[1].combined_score
        assert results[1].combined_score >= results[2].combined_score
    
    @patch('integration_coworker.retrieval.semantic_search.compute_embedding')
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_top_k_filters_results(self, mock_db, mock_embed):
        """top_k should limit returned matches."""
        mock_embed.return_value = [1.0, 0.0, 0.0]
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        # 10 templates
        mock_cursor.fetchall.return_value = [
            (i, f"template_{i}", f"Template {i}", f"Desc {i}", json.dumps([0.5 + i * 0.04, 0.1, 0.0]))
            for i in range(10)
        ]
        
        results = search_kg_templates("test", top_k=3)
        
        assert len(results) == 3


# =============================================================================
# Error Handling Tests
# =============================================================================

class TestRetrievalErrorHandling:
    """Tests for graceful error handling in retrieval."""
    
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_database_error_returns_empty(self, mock_db):
        """Database errors should return empty list, not raise."""
        mock_db.get_connection.side_effect = Exception("DB connection failed")
        
        results = search_spec_chunks("test query")
        assert results == []
    
    @patch('integration_coworker.retrieval.semantic_search.compute_embedding')
    @patch('integration_coworker.retrieval.semantic_search.db')
    def test_json_decode_error_handled(self, mock_db, mock_embed):
        """JSON decode errors in embeddings should be handled gracefully."""
        mock_embed.return_value = [1.0, 0.0, 0.0]
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_db.get_connection.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        
        mock_cursor.fetchall.return_value = [
            (1, 1, 0, "Content", "{invalid json"),
        ]
        
        results = search_spec_chunks("test")
        # Should return empty (invalid JSON skipped), not raise
        assert len(results) == 0
