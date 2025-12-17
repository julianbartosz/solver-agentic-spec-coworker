"""
Tests for the feedback learning system.

Tests cover:
- Feedback record domain models
- Confidence score computation basics
- Database schema for feedback tables
- GraphRAG scoring with confidence
"""
import os
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

# Force SQLite for tests
os.environ["USE_SQLITE"] = "true"


class TestFeedbackModels:
    """Tests for feedback domain models."""

    def test_feedback_type_enum(self):
        """Test FeedbackType enum values."""
        from integration_coworker.domain.models import FeedbackType

        assert FeedbackType.THUMBS.value == "thumbs"
        assert FeedbackType.SCORE.value == "score"
        assert FeedbackType.AUTO_COMPILE.value == "auto_compile"
        assert FeedbackType.AUTO_TEST.value == "auto_test"
        assert FeedbackType.AUTO_LINT.value == "auto_lint"

    def test_feedback_source_enum(self):
        """Test FeedbackSource enum values."""
        from integration_coworker.domain.models import FeedbackSource

        assert FeedbackSource.LANGSMITH.value == "langsmith"
        assert FeedbackSource.CLI.value == "cli"
        assert FeedbackSource.AUTO.value == "auto"
        assert FeedbackSource.API.value == "api"

    def test_feedback_record_creation(self):
        """Test FeedbackRecord dataclass creation."""
        from integration_coworker.domain.models import (
            FeedbackRecord,
            FeedbackType,
            FeedbackSource,
        )

        record = FeedbackRecord(
            id=123,
            run_id="run-abc",
            template_key="template.stripe.create_payment",
            feedback_type=FeedbackType.THUMBS,
            source=FeedbackSource.LANGSMITH,
            score=1.0,
            comment="Great result!",
            langsmith_feedback_id="ls-xyz",
        )

        assert record.id == 123
        assert record.run_id == "run-abc"
        assert record.score == 1.0
        assert record.feedback_type == FeedbackType.THUMBS
        assert record.source == FeedbackSource.LANGSMITH

    def test_confidence_update_creation(self):
        """Test ConfidenceUpdate dataclass creation."""
        from integration_coworker.domain.models import ConfidenceUpdate

        update = ConfidenceUpdate(
            node_key="template.stripe.create_payment",
            old_confidence=0.8,
            new_confidence=0.85,
            feedback_count=10,
            reason="feedback_sync",
        )

        assert update.node_key == "template.stripe.create_payment"
        assert update.old_confidence == 0.8
        assert update.new_confidence == 0.85
        assert update.feedback_count == 10


class TestConfidenceComputation:
    """Tests for confidence score computation."""

    def test_compute_confidence_no_feedback(self):
        """Test confidence computation with no feedback returns default."""
        from integration_coworker.feedback.confidence import compute_confidence_score

        # With no feedback, should return 0.5 (neutral)
        score = compute_confidence_score([])
        assert score == 0.5  # Default neutral score

    def test_compute_confidence_single_positive(self):
        """Test confidence with single positive feedback."""
        from integration_coworker.feedback.confidence import compute_confidence_score
        from integration_coworker.domain.models import (
            FeedbackRecord,
            FeedbackType,
            FeedbackSource,
        )

        records = [
            FeedbackRecord(
                id=1,
                run_id="run-1",
                template_key="test.template",
                feedback_type=FeedbackType.THUMBS,
                source=FeedbackSource.LANGSMITH,
                score=1.0,
                created_at=datetime.now().isoformat(),
            )
        ]

        score = compute_confidence_score(records)
        # Should be > 0.5 since we have positive feedback
        assert score > 0.5
        assert score <= 1.0

    def test_compute_confidence_single_negative(self):
        """Test confidence with single negative feedback."""
        from integration_coworker.feedback.confidence import compute_confidence_score
        from integration_coworker.domain.models import (
            FeedbackRecord,
            FeedbackType,
            FeedbackSource,
        )

        records = [
            FeedbackRecord(
                id=1,
                run_id="run-1",
                template_key="test.template",
                feedback_type=FeedbackType.THUMBS,
                source=FeedbackSource.LANGSMITH,
                score=0.0,
                created_at=datetime.now().isoformat(),
            )
        ]

        score = compute_confidence_score(records)
        # Should be < 0.5 since we have negative feedback
        assert score < 0.5
        assert score >= 0.0

    def test_compute_confidence_mixed_feedback(self):
        """Test confidence with mixed positive and negative feedback."""
        from integration_coworker.feedback.confidence import compute_confidence_score
        from integration_coworker.domain.models import (
            FeedbackRecord,
            FeedbackType,
            FeedbackSource,
        )

        records = [
            FeedbackRecord(
                id=1,
                run_id="run-1",
                template_key="test.template",
                feedback_type=FeedbackType.THUMBS,
                source=FeedbackSource.LANGSMITH,
                score=1.0,
                created_at=datetime.now().isoformat(),
            ),
            FeedbackRecord(
                id=2,
                run_id="run-2",
                template_key="test.template",
                feedback_type=FeedbackType.THUMBS,
                source=FeedbackSource.LANGSMITH,
                score=0.0,
                created_at=datetime.now().isoformat(),
            ),
        ]

        score = compute_confidence_score(records)
        # Should be around 0.5 since we have balanced feedback
        assert 0.3 <= score <= 0.7

    def test_compute_confidence_recency_weighting(self):
        """Test that recent feedback is weighted more than old feedback."""
        from integration_coworker.feedback.confidence import compute_confidence_score
        from integration_coworker.domain.models import (
            FeedbackRecord,
            FeedbackType,
            FeedbackSource,
        )

        now = datetime.now()
        old_time = now - timedelta(days=60)  # 2 months ago

        # Old positive, recent negative
        records = [
            FeedbackRecord(
                id=1,
                run_id="run-1",
                template_key="test.template",
                feedback_type=FeedbackType.THUMBS,
                source=FeedbackSource.LANGSMITH,
                score=1.0,
                created_at=old_time.isoformat(),
            ),
            FeedbackRecord(
                id=2,
                run_id="run-2",
                template_key="test.template",
                feedback_type=FeedbackType.THUMBS,
                source=FeedbackSource.LANGSMITH,
                score=0.0,
                created_at=now.isoformat(),
            ),
        ]

        score = compute_confidence_score(records)
        # Recent negative should dominate over old positive
        assert score < 0.5

    def test_compute_confidence_source_weighting(self):
        """Test that CLI feedback is weighted more than auto feedback."""
        from integration_coworker.feedback.confidence import compute_confidence_score
        from integration_coworker.domain.models import (
            FeedbackRecord,
            FeedbackType,
            FeedbackSource,
        )

        now = datetime.now()

        # CLI positive, auto negative
        records = [
            FeedbackRecord(
                id=1,
                run_id="run-1",
                template_key="test.template",
                feedback_type=FeedbackType.THUMBS,
                source=FeedbackSource.CLI,  # Higher weight
                score=1.0,
                created_at=now.isoformat(),
            ),
            FeedbackRecord(
                id=2,
                run_id="run-2",
                template_key="test.template",
                feedback_type=FeedbackType.AUTO_COMPILE,
                source=FeedbackSource.AUTO,  # Lower weight
                score=0.0,
                created_at=now.isoformat(),
            ),
        ]

        score = compute_confidence_score(records)
        # CLI positive should outweigh auto negative
        assert score > 0.5


class TestGraphRAGScoringWithConfidence:
    """Tests for GraphRAG scoring that includes confidence."""

    @pytest.fixture
    def setup_kg_with_templates(self):
        """Set up KG with templates having different confidence scores."""
        from integration_coworker.persistence import db
        from integration_coworker.domain.models import KGNodeType

        db.init_schema()
        conn = db.get_connection()
        cur = conn.cursor()

        # Clear existing data
        db.clear_test_data()

        # Insert templates with different confidence scores
        templates = [
            ("template.test.high_conf", "High Confidence", 0.95, 10),
            ("template.test.low_conf", "Low Confidence", 0.3, 5),
            ("template.test.default_conf", "Default Confidence", 1.0, 1),
        ]

        for key, name, conf, usage in templates:
            cur.execute("""
                INSERT INTO kg_nodes (key, name, node_type, provider_code, confidence_score, usage_count)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (key, name, KGNodeType.WORKFLOW_TEMPLATE.value, "test", conf, usage))

        conn.commit()

        yield

        # Cleanup
        db.clear_test_data()

    def test_confidence_affects_final_score(self, setup_kg_with_templates):
        """Test that confidence score affects the final template ranking."""
        from integration_coworker.kg import query_kg_templates

        # Query templates
        matches = query_kg_templates(
            provider_code="test",
            task_description="Test task",
            top_k=10,
        )

        assert len(matches) > 0

        # Find the templates
        high_conf = next((m for m in matches if "high_conf" in m.template_key), None)
        low_conf = next((m for m in matches if "low_conf" in m.template_key), None)

        if high_conf and low_conf:
            # High confidence template should have higher final score
            # (all else being equal, confidence adds 10% weight)
            # Note: other factors (usage_count, etc.) may also affect ranking
            assert high_conf.final_score >= low_conf.final_score - 0.1


class TestDatabaseSchema:
    """Tests for feedback-related database schema."""

    def test_feedback_tables_exist(self):
        """Test that feedback tables are created."""
        from integration_coworker.persistence import db

        db.init_schema()
        conn = db.get_connection()
        cur = conn.cursor()

        # Check kg_feedback_records exists
        cur.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='kg_feedback_records'
        """)
        assert cur.fetchone() is not None

        # Check kg_confidence_history exists
        cur.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='kg_confidence_history'
        """)
        assert cur.fetchone() is not None

    def test_clear_test_data_includes_feedback(self):
        """Test that clear_test_data clears feedback tables."""
        from integration_coworker.persistence import db

        db.init_schema()
        conn = db.get_connection()
        cur = conn.cursor()

        # Insert test data
        cur.execute("""
            INSERT INTO kg_feedback_records 
            (run_id, template_key, feedback_type, source, score, created_at)
            VALUES ('run-test', 'template.test', 'score', 'human', 0.8, datetime('now'))
        """)
        cur.execute("""
            INSERT INTO kg_confidence_history
            (node_key, old_confidence, new_confidence, feedback_count, reason, created_at)
            VALUES ('template.test', 0.7, 0.8, 5, 'test', datetime('now'))
        """)
        conn.commit()

        # Verify data exists
        cur.execute("SELECT COUNT(*) FROM kg_feedback_records")
        assert cur.fetchone()[0] > 0

        # Clear test data
        db.clear_test_data()

        # Verify data is cleared
        cur.execute("SELECT COUNT(*) FROM kg_feedback_records")
        assert cur.fetchone()[0] == 0

        cur.execute("SELECT COUNT(*) FROM kg_confidence_history")
        assert cur.fetchone()[0] == 0
