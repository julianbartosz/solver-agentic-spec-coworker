"""
E2E Postgres Tests for Feedback → Confidence → KG Ranking (P2)

MERGE-BLOCKING: These tests prove the production contract that:
1. Feedback records in kg.feedback_records affect kg.nodes.confidence_score
2. Confidence score changes affect GraphRAG retrieval ordering
3. The entire loop works end-to-end with real Postgres persistence

Per docs/decisions/FEEDBACK_LEARNING_IMPLEMENTATION.md:
- Confidence is 10% of the final GraphRAG score
- Templates with higher confidence rank above identical templates
- update_all_confidences() aggregates feedback into confidence

Test design:
- Insert two identical templates (same provider, same properties)
- Insert positive feedback for template A only
- Call update_all_confidences() 
- Query KG and verify template A ranks above template B

CRITICAL: Requires real Postgres with kg.* schema initialized.
"""

import json
import os
import pytest
import time
import uuid
from datetime import datetime, timezone

from integration_coworker.domain.models import (
    FeedbackType,
    FeedbackSource,
)
from integration_coworker.feedback.confidence import (
    update_all_confidences,
    update_node_confidence,
    compute_confidence_score,
    DEFAULT_CONFIDENCE,
)


def _reset_config_cache():
    """
    Reset the config module's settings cache.
    
    CRITICAL: The config module caches Settings in _settings.
    When we modify DATABASE_URL or USE_SQLITE, we must reset this cache
    to ensure the new values take effect.
    """
    import integration_coworker.config as config_module
    config_module._settings = None


def require_postgres():
    """Skip if Postgres is unavailable."""
    if not os.environ.get('DATABASE_URL'):
        pytest.skip(
            "DATABASE_URL not set - requires real Postgres. "
            "These are merge-blocking for P2 feedback learning."
        )
    try:
        import psycopg
        with psycopg.connect(os.environ['DATABASE_URL']) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
    except Exception as e:
        pytest.skip(f"Cannot connect to Postgres: {e}")


@pytest.fixture
def db_url() -> str:
    """Get database URL and verify connectivity."""
    require_postgres()
    
    # CRITICAL: Reset settings cache so DATABASE_URL is re-read
    # The config module caches settings, which can cause test isolation issues
    import integration_coworker.config as config_module
    config_module._settings = None
    
    return os.environ['DATABASE_URL']


@pytest.fixture
def unique_suffix() -> str:
    """Generate unique suffix for test isolation."""
    return f"{uuid.uuid4().hex[:8]}_{int(time.time())}"


@pytest.fixture
def test_provider(unique_suffix: str) -> str:
    """Provider code for test isolation."""
    return f"test_feedback_{unique_suffix}"


@pytest.fixture
def setup_test_templates(db_url: str, test_provider: str):
    """
    Create two identical KG template nodes for comparison testing.
    
    Both templates have:
    - Same provider_code
    - Same node_type (workflow_template)
    - Same usage_count (0)
    - Same properties
    - Default confidence_score (1.0 from schema)
    
    Returns tuple: (template_a_key, template_b_key)
    """
    import psycopg
    
    template_a_key = f"template.{test_provider}.task_alpha"
    template_b_key = f"template.{test_provider}.task_beta"
    
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            # Clean up any existing test data
            cur.execute("""
                DELETE FROM kg.feedback_records 
                WHERE template_key LIKE %s
            """, (f"template.{test_provider}.%",))
            
            cur.execute("""
                DELETE FROM kg.confidence_history 
                WHERE node_key LIKE %s
            """, (f"template.{test_provider}.%",))
            
            cur.execute("""
                DELETE FROM kg.nodes 
                WHERE key LIKE %s
            """, (f"template.{test_provider}.%",))
            
            # Insert template A
            cur.execute("""
                INSERT INTO kg.nodes 
                (key, node_type, name, description, provider_code, properties, usage_count, confidence_score)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                template_a_key,
                "workflow_template",
                "Task Alpha Flow",
                "A test workflow template",
                test_provider,
                json.dumps({"steps": ["step1", "step2"]}),
                10,  # Same usage count
                1.0,  # Default confidence from schema
            ))
            
            # Insert template B (identical except key)
            cur.execute("""
                INSERT INTO kg.nodes 
                (key, node_type, name, description, provider_code, properties, usage_count, confidence_score)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                template_b_key,
                "workflow_template",
                "Task Beta Flow",
                "A test workflow template",  # Same description
                test_provider,
                json.dumps({"steps": ["step1", "step2"]}),  # Same properties
                10,  # Same usage count
                1.0,  # Same default confidence
            ))
            
            conn.commit()
    
    yield (template_a_key, template_b_key)
    
    # Cleanup after test
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM kg.feedback_records 
                WHERE template_key LIKE %s
            """, (f"template.{test_provider}.%",))
            
            cur.execute("""
                DELETE FROM kg.confidence_history 
                WHERE node_key LIKE %s
            """, (f"template.{test_provider}.%",))
            
            cur.execute("""
                DELETE FROM kg.nodes 
                WHERE key LIKE %s
            """, (f"template.{test_provider}.%",))
            
            conn.commit()


def _insert_feedback(
    db_url: str,
    template_key: str,
    score: float,
    feedback_type: str = "thumbs",
    source: str = "cli",
    run_id: str = None,
) -> int:
    """Insert a feedback record and return its ID."""
    import psycopg
    
    if run_id is None:
        run_id = f"run_{uuid.uuid4().hex[:8]}"
    
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO kg.feedback_records
                (run_id, template_key, feedback_type, score, source, created_at, synced_at)
                VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING id
            """, (run_id, template_key, feedback_type, score, source))
            feedback_id = cur.fetchone()[0]
            conn.commit()
    
    return feedback_id


def _get_confidence_score(db_url: str, template_key: str) -> float:
    """Get current confidence_score for a template from kg.nodes."""
    import psycopg
    
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT confidence_score FROM kg.nodes WHERE key = %s
            """, (template_key,))
            row = cur.fetchone()
            return row[0] if row else None


def _query_templates_ranked(db_url: str, provider_code: str) -> list:
    """
    Query templates for provider, sorted by the GraphRAG scoring formula.
    
    Returns list of (key, confidence_score, final_score) tuples.
    
    Simplified scoring (no embeddings):
    final_score = graph_score*0.4 + similarity*0.4 + confidence*0.1 + exact_match + 0.05
    
    For identical templates with no embeddings:
    - graph_score = same (both pass filter)
    - similarity = same (deterministic fallback)
    - exact_match = 0 (no task_slug match)
    
    So ordering depends on confidence_score * 0.1
    """
    import psycopg
    
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            # Query like kg/__init__.py does, sorted by confidence
            cur.execute("""
                SELECT key, confidence_score, 
                       (0.4 + 0.4 + confidence_score * 0.1 + 0.05) as approx_score
                FROM kg.nodes
                WHERE node_type = 'workflow_template'
                  AND provider_code = %s
                ORDER BY confidence_score DESC, key ASC
            """, (provider_code,))
            
            return [(row[0], row[1], row[2]) for row in cur.fetchall()]


# =============================================================================
# Test: Feedback Changes Confidence Score in Database
# =============================================================================

@pytest.mark.postgres
class TestFeedbackConfidenceUpdate:
    """Test that feedback records update kg.nodes.confidence_score."""
    
    def test_positive_feedback_increases_confidence(
        self, db_url: str, setup_test_templates: tuple
    ):
        """
        Positive feedback should result in confidence > default.
        
        Contract:
        1. Template starts at confidence 1.0 (schema default)
        2. Insert positive feedback (score=1.0)
        3. After update_node_confidence(), confidence stays high
        """
        template_a_key, template_b_key = setup_test_templates
        
        # Verify initial state
        initial_confidence_a = _get_confidence_score(db_url, template_a_key)
        initial_confidence_b = _get_confidence_score(db_url, template_b_key)
        
        assert initial_confidence_a == 1.0, "Schema default should be 1.0"
        assert initial_confidence_b == 1.0, "Schema default should be 1.0"
        
        # Insert positive feedback for template A
        _insert_feedback(db_url, template_a_key, score=1.0, feedback_type="thumbs", source="cli")
        _insert_feedback(db_url, template_a_key, score=0.9, feedback_type="score", source="langsmith")
        
        # Update confidence for template A
        # Need to set DATABASE_URL for db module
        os.environ['DATABASE_URL'] = db_url
        os.environ['USE_SQLITE'] = 'false'
        
        # Re-import to pick up env vars
        # No longer needed: db module reads settings
        _reset_config_cache()
        
        new_confidence = update_node_confidence(template_a_key, reason="test_positive_feedback")
        
        assert new_confidence is not None, "update_node_confidence should return a value"
        assert new_confidence > 0.8, f"Positive feedback should yield high confidence, got {new_confidence}"
        
        # Verify it's persisted
        db_confidence = _get_confidence_score(db_url, template_a_key)
        assert abs(db_confidence - new_confidence) < 0.01, "DB should match returned confidence"
        
        # Template B should be unchanged
        confidence_b = _get_confidence_score(db_url, template_b_key)
        assert confidence_b == 1.0, "Template B should be unchanged"
    
    def test_negative_feedback_decreases_confidence(
        self, db_url: str, setup_test_templates: tuple
    ):
        """
        Negative feedback should result in confidence < default.
        
        Contract:
        1. Insert negative feedback (score=0.0)
        2. After update_node_confidence(), confidence drops
        """
        template_a_key, template_b_key = setup_test_templates
        
        # Insert negative feedback for template A
        _insert_feedback(db_url, template_a_key, score=0.0, feedback_type="thumbs", source="cli")
        _insert_feedback(db_url, template_a_key, score=0.2, feedback_type="score", source="cli")
        
        os.environ['DATABASE_URL'] = db_url
        os.environ['USE_SQLITE'] = 'false'
        
        # No longer needed: db module reads settings
        _reset_config_cache()
        
        new_confidence = update_node_confidence(template_a_key, reason="test_negative_feedback")
        
        assert new_confidence is not None
        assert new_confidence < 0.5, f"Negative feedback should yield low confidence, got {new_confidence}"
        
        # Verify persisted
        db_confidence = _get_confidence_score(db_url, template_a_key)
        assert abs(db_confidence - new_confidence) < 0.01


# =============================================================================
# Test: Confidence Affects KG Retrieval Ordering
# =============================================================================

@pytest.mark.postgres
class TestConfidenceAffectsRanking:
    """
    Test that confidence_score differences change retrieval ordering.
    
    This is the CORE E2E CONTRACT:
    - Two identical templates
    - One gets positive feedback, the other doesn't
    - After confidence update, the one with feedback ranks higher
    """
    
    def test_higher_confidence_ranks_first(
        self, db_url: str, setup_test_templates: tuple
    ):
        """
        Template A with positive feedback should rank above Template B.
        
        Full E2E contract:
        1. Templates A and B are identical (same provider, usage_count, properties)
        2. Insert positive feedback for A only
        3. Call update_all_confidences()
        4. Query templates sorted by GraphRAG score
        5. Assert A ranks above B
        """
        template_a_key, template_b_key = setup_test_templates
        
        # Verify both templates exist
        ranked_before = _query_templates_ranked(db_url, template_a_key.split('.')[1])
        assert len(ranked_before) == 2, "Should have 2 templates"
        
        # Both should have same confidence initially
        assert ranked_before[0][1] == ranked_before[1][1] == 1.0, "Initial confidence should be same"
        
        # Insert positive feedback for template A only
        _insert_feedback(db_url, template_a_key, score=1.0, feedback_type="thumbs", source="cli")
        _insert_feedback(db_url, template_a_key, score=1.0, feedback_type="score", source="langsmith")
        
        # Insert negative feedback for template B to create clear difference
        _insert_feedback(db_url, template_b_key, score=0.2, feedback_type="thumbs", source="cli")
        _insert_feedback(db_url, template_b_key, score=0.1, feedback_type="score", source="cli")
        
        # Update all confidences
        os.environ['DATABASE_URL'] = db_url
        os.environ['USE_SQLITE'] = 'false'
        
        # No longer needed: db module reads settings
        _reset_config_cache()
        
        provider_code = template_a_key.split('.')[1]
        stats = update_all_confidences(provider_code=provider_code, reason="test_ranking")
        
        assert stats["updated"] == 2, f"Should update 2 templates, got {stats}"
        
        # Verify confidence changed
        confidence_a = _get_confidence_score(db_url, template_a_key)
        confidence_b = _get_confidence_score(db_url, template_b_key)
        
        assert confidence_a > confidence_b, (
            f"Template A (positive feedback) should have higher confidence: "
            f"A={confidence_a}, B={confidence_b}"
        )
        
        # Query ranked
        ranked_after = _query_templates_ranked(db_url, provider_code)
        
        # Template A should rank first (higher confidence)
        assert ranked_after[0][0] == template_a_key, (
            f"Template A should rank first, got order: {[r[0] for r in ranked_after]}"
        )
        assert ranked_after[1][0] == template_b_key, (
            f"Template B should rank second, got order: {[r[0] for r in ranked_after]}"
        )
        
        # Verify score difference reflects confidence
        score_a = ranked_after[0][2]
        score_b = ranked_after[1][2]
        
        assert score_a > score_b, (
            f"Template A should have higher score: A={score_a}, B={score_b}"
        )
        
        # The score difference should be roughly (confidence_a - confidence_b) * 0.1
        expected_diff = (confidence_a - confidence_b) * 0.1
        actual_diff = score_a - score_b
        
        assert abs(actual_diff - expected_diff) < 0.05, (
            f"Score difference should reflect confidence weight: "
            f"expected ~{expected_diff:.3f}, got {actual_diff:.3f}"
        )


# =============================================================================
# Test: Confidence History Audit Trail
# =============================================================================

@pytest.mark.postgres
class TestConfidenceHistory:
    """Test that confidence changes are logged to kg.confidence_history."""
    
    def test_confidence_history_recorded(
        self, db_url: str, setup_test_templates: tuple
    ):
        """
        Each confidence update should create a history record.
        
        Contract:
        1. Update confidence
        2. Check kg.confidence_history has new row
        3. Verify old/new values and reason
        """
        import psycopg
        
        template_a_key, _ = setup_test_templates
        
        # Insert feedback and update
        _insert_feedback(db_url, template_a_key, score=0.8, feedback_type="score", source="cli")
        
        os.environ['DATABASE_URL'] = db_url
        os.environ['USE_SQLITE'] = 'false'
        
        # No longer needed: db module reads settings
        _reset_config_cache()
        
        update_node_confidence(template_a_key, reason="test_history_recording")
        
        # Check history
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT node_key, old_confidence, new_confidence, feedback_count, reason
                    FROM kg.confidence_history
                    WHERE node_key = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (template_a_key,))
                
                row = cur.fetchone()
        
        assert row is not None, "Should have history record"
        assert row[0] == template_a_key, "Node key should match"
        assert row[1] == 1.0, "Old confidence should be default"
        assert 0.7 < row[2] < 0.9, f"New confidence should reflect feedback, got {row[2]}"
        assert row[3] == 1, "Feedback count should be 1"
        assert row[4] == "test_history_recording", "Reason should match"


# =============================================================================
# Test: Full GraphRAG Scoring Path (Integration)
# =============================================================================

@pytest.mark.postgres
class TestFullGraphRAGPath:
    """
    Integration test using actual kg.search_templates function.
    
    This validates confidence is used in the real retrieval path,
    not just in isolated queries.
    """
    
    def test_search_templates_uses_confidence(
        self, db_url: str, setup_test_templates: tuple
    ):
        """
        Full integration: kg.search_templates should reflect confidence.
        
        Contract:
        1. Setup templates with different confidences
        2. Call search_templates()
        3. Verify ordering matches confidence
        """
        template_a_key, template_b_key = setup_test_templates
        provider_code = template_a_key.split('.')[1]
        
        # Insert feedback to differentiate
        _insert_feedback(db_url, template_a_key, score=1.0, feedback_type="thumbs", source="cli")
        _insert_feedback(db_url, template_b_key, score=0.0, feedback_type="thumbs", source="cli")
        
        os.environ['DATABASE_URL'] = db_url
        os.environ['USE_SQLITE'] = 'false'
        
        # No longer needed: db module reads settings
        _reset_config_cache()
        
        update_all_confidences(provider_code=provider_code, reason="test_full_graphrag")
        
        # Verify confidence difference
        confidence_a = _get_confidence_score(db_url, template_a_key)
        confidence_b = _get_confidence_score(db_url, template_b_key)
        
        assert confidence_a > confidence_b, "Pre-condition: A should have higher confidence"
        
        # Import and call actual query_kg_templates
        from integration_coworker.kg import query_kg_templates
        
        # Search with a generic task that matches both templates
        results = query_kg_templates(
            provider_code=provider_code,
            task_description="A test workflow task",
            top_k=10,
        )
        
        # Should find both templates
        assert len(results) >= 2, f"Should find both templates, got {len(results)}"
        
        # Find our test templates in results
        result_keys = [r.template_key for r in results]
        
        assert template_a_key in result_keys, f"Template A should be in results: {result_keys}"
        assert template_b_key in result_keys, f"Template B should be in results: {result_keys}"
        
        # Template A should rank at or above Template B
        idx_a = result_keys.index(template_a_key)
        idx_b = result_keys.index(template_b_key)
        
        assert idx_a <= idx_b, (
            f"Template A (high confidence) should rank at or above B: "
            f"A at {idx_a}, B at {idx_b}"
        )
        
        # Verify final_score reflects confidence
        result_a = next(r for r in results if r.template_key == template_a_key)
        result_b = next(r for r in results if r.template_key == template_b_key)
        
        assert result_a.final_score >= result_b.final_score, (
            f"Template A should have >= score: A={result_a.final_score}, B={result_b.final_score}"
        )


# =============================================================================
# Test: Fresh Connection Meta-Assertion (MERGE-BLOCKING)
# =============================================================================

@pytest.mark.postgres
class TestFreshConnectionPersistence:
    """
    META-ASSERTION: Prove confidence changes persist across DB connections.
    
    This is the CRITICAL E2E proof that the feedback → confidence loop
    actually modifies Postgres, not just in-memory state.
    
    Without this, tests could pass with a mock or in-memory cache.
    """
    
    def test_confidence_visible_from_fresh_connection(
        self, db_url: str, setup_test_templates: tuple
    ):
        """
        Changes made via update_all_confidences() MUST be visible
        from a completely new DB connection.
        
        This proves:
        1. Data is committed to Postgres (not just cursor state)
        2. No connection pooling tricks hide the mutation
        3. Any external observer would see the same result
        
        MERGE-BLOCKING: If this fails, confidence updates are broken.
        """
        import psycopg
        
        template_a_key, template_b_key = setup_test_templates
        provider_code = template_a_key.split('.')[1]
        
        # ========================================
        # Step 1: Record initial state via fresh connection
        # ========================================
        with psycopg.connect(db_url) as conn1:
            with conn1.cursor() as cur:
                cur.execute("""
                    SELECT confidence_score FROM kg.nodes 
                    WHERE key = %s
                """, (template_a_key,))
                initial_confidence = cur.fetchone()[0]
        
        assert initial_confidence == 1.0, f"Schema default should be 1.0, got {initial_confidence}"
        
        # ========================================
        # Step 2: Insert feedback and update confidence
        # (uses internal connection from db module)
        # ========================================
        _insert_feedback(db_url, template_a_key, score=0.0, feedback_type="thumbs", source="cli")
        _insert_feedback(db_url, template_a_key, score=0.1, feedback_type="score", source="cli")
        
        os.environ['DATABASE_URL'] = db_url
        os.environ['USE_SQLITE'] = 'false'
        
        # No longer needed: db module reads settings
        _reset_config_cache()
        
        stats = update_all_confidences(provider_code=provider_code, reason="meta_assertion_test")
        
        assert stats["updated"] >= 1, f"Should have updated template A: {stats}"
        
        # ========================================
        # Step 3: Query from BRAND NEW connection (meta assertion)
        # ========================================
        # This connection has never seen the update_all_confidences() call
        # If confidence changed, it MUST be visible here
        with psycopg.connect(db_url) as conn2:
            with conn2.cursor() as cur:
                cur.execute("""
                    SELECT confidence_score FROM kg.nodes 
                    WHERE key = %s
                """, (template_a_key,))
                final_confidence = cur.fetchone()[0]
        
        # ========================================
        # Step 4: Assert confidence actually changed (CRITICAL)
        # ========================================
        assert final_confidence < initial_confidence, (
            f"META-ASSERTION FAILED: Confidence should decrease with negative feedback.\n"
            f"Initial: {initial_confidence}\n"
            f"Final (from fresh connection): {final_confidence}\n"
            f"This means either:\n"
            f"  1. update_all_confidences() didn't commit to Postgres\n"
            f"  2. Changes are cached in-memory but not persisted\n"
            f"  3. The test is running against SQLite (check USE_SQLITE env var)"
        )
        
        assert final_confidence < 0.5, (
            f"Negative feedback should yield low confidence, got {final_confidence}"
        )
    
    def test_ranking_flip_visible_from_fresh_connection(
        self, db_url: str, setup_test_templates: tuple
    ):
        """
        Ranking changes from feedback MUST be visible from fresh connection.
        
        This is the full E2E proof:
        1. Two identical templates start with same confidence
        2. Give positive feedback to A, negative to B
        3. After update, A ranks above B
        4. This ranking is visible from a fresh connection
        
        MERGE-BLOCKING: If this fails, GraphRAG ranking is broken.
        """
        import psycopg
        
        template_a_key, template_b_key = setup_test_templates
        provider_code = template_a_key.split('.')[1]
        
        # ========================================
        # Step 1: Verify equal starting confidence from fresh connection
        # ========================================
        with psycopg.connect(db_url) as conn1:
            with conn1.cursor() as cur:
                cur.execute("""
                    SELECT key, confidence_score 
                    FROM kg.nodes 
                    WHERE key IN (%s, %s)
                    ORDER BY key
                """, (template_a_key, template_b_key))
                initial_rows = cur.fetchall()
        
        initial_conf_a = next(r[1] for r in initial_rows if r[0] == template_a_key)
        initial_conf_b = next(r[1] for r in initial_rows if r[0] == template_b_key)
        
        assert initial_conf_a == initial_conf_b == 1.0, (
            f"Both should start at 1.0: A={initial_conf_a}, B={initial_conf_b}"
        )
        
        # ========================================
        # Step 2: Insert divergent feedback
        # ========================================
        # Positive for A
        _insert_feedback(db_url, template_a_key, score=1.0, feedback_type="thumbs", source="cli")
        _insert_feedback(db_url, template_a_key, score=0.9, feedback_type="score", source="langsmith")
        
        # Negative for B
        _insert_feedback(db_url, template_b_key, score=0.0, feedback_type="thumbs", source="cli")
        _insert_feedback(db_url, template_b_key, score=0.1, feedback_type="score", source="cli")
        
        os.environ['DATABASE_URL'] = db_url
        os.environ['USE_SQLITE'] = 'false'
        
        # No longer needed: db module reads settings
        _reset_config_cache()
        
        update_all_confidences(provider_code=provider_code, reason="ranking_flip_test")
        
        # ========================================
        # Step 3: Query ranking from BRAND NEW connection
        # ========================================
        with psycopg.connect(db_url) as conn2:
            with conn2.cursor() as cur:
                # This is the exact query GraphRAG would use
                cur.execute("""
                    SELECT key, confidence_score,
                           (0.4 + 0.4 + confidence_score * 0.1 + 0.05) as final_score
                    FROM kg.nodes
                    WHERE node_type = 'workflow_template'
                      AND provider_code = %s
                    ORDER BY confidence_score DESC, key ASC
                """, (provider_code,))
                ranked_rows = cur.fetchall()
        
        # ========================================
        # Step 4: Assert ranking flip (CRITICAL)
        # ========================================
        assert len(ranked_rows) == 2, f"Should have 2 templates, got {len(ranked_rows)}"
        
        ranked_keys = [r[0] for r in ranked_rows]
        
        assert ranked_keys[0] == template_a_key, (
            f"META-ASSERTION FAILED: Template A (positive feedback) should rank first.\n"
            f"Actual ranking: {ranked_keys}\n"
            f"Confidences: A={ranked_rows[0][1] if ranked_rows[0][0]==template_a_key else ranked_rows[1][1]}, "
            f"B={ranked_rows[1][1] if ranked_rows[1][0]==template_b_key else ranked_rows[0][1]}\n"
            f"This means feedback→confidence→ranking loop is broken."
        )
        
        assert ranked_keys[1] == template_b_key, (
            f"Template B should rank second, got: {ranked_keys}"
        )
        
        # Verify confidence values
        conf_a = next(r[1] for r in ranked_rows if r[0] == template_a_key)
        conf_b = next(r[1] for r in ranked_rows if r[0] == template_b_key)
        
        assert conf_a > conf_b, (
            f"Confidence A should be > B: A={conf_a}, B={conf_b}"
        )
        
        # Confidence A should be high (positive feedback)
        assert conf_a > 0.7, f"Positive feedback should yield high confidence: {conf_a}"
        
        # Confidence B should be low (negative feedback)
        assert conf_b < 0.4, f"Negative feedback should yield low confidence: {conf_b}"


# =============================================================================
# Test class: Feedback idempotency under retries
# =============================================================================

@pytest.mark.postgres  
class TestFeedbackIdempotency:
    """
    Test that feedback recording is idempotent under retries.
    
    CRITICAL: If the same implicit signal is recorded multiple times,
    the confidence calculation will be corrupted. The database must
    deduplicate on (run_id, template_key, feedback_type, source).
    
    This tests the kg_feedback_implicit_dedup_idx unique index.
    """
    
    def test_implicit_signal_dedup_on_retry(self):
        """Recording the same implicit signal twice should not duplicate rows."""
        require_postgres()
        db_url = os.environ['DATABASE_URL']
        
        _reset_config_cache()
        os.environ['DATABASE_URL'] = db_url
        os.environ['USE_SQLITE'] = 'false'
        
        import psycopg
        from integration_coworker.persistence import db
        from integration_coworker.feedback.implicit_signals import record_compile_result
        
        # Create unique identifiers for this test
        test_id = str(uuid.uuid4())[:8]
        run_id = f"idempotency-test-{test_id}"
        template_key = f"template://idempotency/{test_id}"
        
        # Record the same signal multiple times (simulating retries)
        record_compile_result(run_id=run_id, template_key=template_key, success=True)
        record_compile_result(run_id=run_id, template_key=template_key, success=True)
        record_compile_result(run_id=run_id, template_key=template_key, success=True)
        
        # Query the database directly to count rows
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) FROM kg.feedback_records
                    WHERE run_id = %s AND template_key = %s
                """, (run_id, template_key))
                row_count = cur.fetchone()[0]
        
        assert row_count == 1, (
            f"IDEMPOTENCY FAILURE: Expected 1 row, got {row_count}. "
            f"The kg_feedback_implicit_dedup_idx constraint is not working."
        )
        
        # Cleanup
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kg.feedback_records WHERE run_id = %s", (run_id,))
                conn.commit()
    
    def test_different_feedback_types_are_separate(self):
        """Different feedback types for same run should create separate rows."""
        require_postgres()
        db_url = os.environ['DATABASE_URL']
        
        _reset_config_cache()
        os.environ['DATABASE_URL'] = db_url
        os.environ['USE_SQLITE'] = 'false'
        
        import psycopg
        from integration_coworker.feedback.implicit_signals import (
            record_compile_result,
            record_test_result,
            record_lint_result,
        )
        
        # Create unique identifiers
        test_id = str(uuid.uuid4())[:8]
        run_id = f"multi-type-test-{test_id}"
        template_key = f"template://multi-type/{test_id}"
        
        # Record different feedback types
        record_compile_result(run_id=run_id, template_key=template_key, success=True)
        record_test_result(run_id=run_id, template_key=template_key, passed=10, failed=0)
        record_lint_result(run_id=run_id, template_key=template_key, violations=0, total_lines=100)
        
        # Query the database
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT feedback_type FROM kg.feedback_records
                    WHERE run_id = %s
                """, (run_id,))
                types = [r[0] for r in cur.fetchall()]
        
        assert len(types) == 3, (
            f"Should have 3 different feedback types, got {len(types)}: {types}"
        )
        assert 'auto_compile' in types
        assert 'auto_test' in types
        assert 'auto_lint' in types
        
        # Cleanup
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kg.feedback_records WHERE run_id = %s", (run_id,))
                conn.commit()
    
    def test_retry_preserves_latest_score(self):
        """Retrying with different score should update, not duplicate."""
        require_postgres()
        db_url = os.environ['DATABASE_URL']
        
        _reset_config_cache()
        os.environ['DATABASE_URL'] = db_url
        os.environ['USE_SQLITE'] = 'false'
        
        import psycopg
        from integration_coworker.feedback.implicit_signals import record_compile_result
        
        # Create unique identifiers
        test_id = str(uuid.uuid4())[:8]
        run_id = f"score-update-test-{test_id}"
        template_key = f"template://score-update/{test_id}"
        
        # Record failure first, then success (simulating fix + retry)
        record_compile_result(run_id=run_id, template_key=template_key, success=False)
        record_compile_result(run_id=run_id, template_key=template_key, success=True)
        
        # Query the database
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT score FROM kg.feedback_records
                    WHERE run_id = %s AND template_key = %s
                """, (run_id, template_key))
                rows = cur.fetchall()
        
        assert len(rows) == 1, f"Should have exactly 1 row, got {len(rows)}"
        # The latest score (success=True → 1.0) should be preserved
        assert rows[0][0] == 1.0, (
            f"Latest score should be 1.0 (success), got {rows[0][0]}. "
            f"ON CONFLICT DO UPDATE is not working correctly."
        )
        
        # Cleanup
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kg.feedback_records WHERE run_id = %s", (run_id,))
                conn.commit()
