"""
Tests for streaming progress tracking.

See docs/PROD_GAP_CLOSURE_PLAN_KG_DB_STREAMING.md Phase 3 for design rationale.
"""

import pytest
import os
import tempfile
from unittest.mock import patch, MagicMock

from integration_coworker.persistence.streaming import (
    save_streaming_progress,
    load_streaming_progress,
    clear_streaming_progress,
    stream_embeddings_with_progress,
    init_streaming_progress_table,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_sqlite_db():
    """Create a temporary SQLite database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    yield db_path
    
    # Cleanup
    try:
        os.unlink(db_path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Unit Tests: Progress Save/Load/Clear
# ---------------------------------------------------------------------------

class TestProgressSaveLoad:
    """Tests for save_streaming_progress and load_streaming_progress."""
    
    @patch("integration_coworker.persistence.streaming.get_engine_type")
    @patch("integration_coworker.persistence.streaming.get_connection")
    def test_save_progress_creates_record(self, mock_get_conn, mock_engine):
        """save_streaming_progress should create a new progress record."""
        mock_engine.return_value = "sqlite"
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        
        result = save_streaming_progress(
            run_id="test-run-123",
            phase="embeddings",
            last_committed_id=50,
            total_items=100,
        )
        
        # Should have executed SQL
        assert mock_cursor.execute.call_count >= 1
        assert mock_conn.commit.called
        assert mock_conn.close.called
    
    @patch("integration_coworker.persistence.streaming.get_engine_type")
    @patch("integration_coworker.persistence.streaming.get_connection")
    def test_load_progress_returns_dict(self, mock_get_conn, mock_engine):
        """load_streaming_progress should return progress dict."""
        mock_engine.return_value = "sqlite"
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        # Simulate a stored row
        mock_cursor.fetchone.return_value = (
            "test-run-123", "embeddings", 50, 100, "2025-01-01", "2025-01-01"
        )
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        
        result = load_streaming_progress("test-run-123", "embeddings")
        
        assert result is not None
        assert result["run_id"] == "test-run-123"
        assert result["phase"] == "embeddings"
        assert result["last_committed_id"] == 50
        assert result["total_items"] == 100
    
    @patch("integration_coworker.persistence.streaming.get_engine_type")
    @patch("integration_coworker.persistence.streaming.get_connection")
    def test_load_progress_returns_none_when_not_found(self, mock_get_conn, mock_engine):
        """load_streaming_progress should return None if no record exists."""
        mock_engine.return_value = "sqlite"
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        
        result = load_streaming_progress("nonexistent-run", "embeddings")
        
        assert result is None
    
    @patch("integration_coworker.persistence.streaming.get_engine_type")
    @patch("integration_coworker.persistence.streaming.get_connection")
    def test_clear_progress_deletes_record(self, mock_get_conn, mock_engine):
        """clear_streaming_progress should delete the progress record."""
        mock_engine.return_value = "sqlite"
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        
        result = clear_streaming_progress("test-run-123", "embeddings")
        
        # Should have executed DELETE
        assert mock_cursor.execute.called
        assert mock_conn.commit.called
        assert result is True
    
    @patch("integration_coworker.persistence.streaming.get_engine_type")
    @patch("integration_coworker.persistence.streaming.get_connection")
    def test_clear_progress_all_phases(self, mock_get_conn, mock_engine):
        """clear_streaming_progress without phase should clear all phases."""
        mock_engine.return_value = "sqlite"
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        
        result = clear_streaming_progress("test-run-123")  # No phase
        
        assert result is True
        # Should have executed without phase filter
        call_args = mock_cursor.execute.call_args
        assert "phase" not in call_args[0][0] or "phase = ?" not in call_args[0][0]


# ---------------------------------------------------------------------------
# Integration Tests: Streaming with Progress
# ---------------------------------------------------------------------------

class TestStreamEmbeddingsWithProgress:
    """Tests for stream_embeddings_with_progress."""
    
    @patch("integration_coworker.persistence.streaming.load_streaming_progress")
    @patch("integration_coworker.persistence.streaming.save_streaming_progress")
    @patch("integration_coworker.persistence.streaming.clear_streaming_progress")
    @patch("integration_coworker.persistence.streaming.get_engine_type")
    @patch("integration_coworker.persistence.streaming.get_connection")
    def test_streams_all_embeddings(
        self, mock_get_conn, mock_engine, mock_clear, mock_save, mock_load
    ):
        """Should stream all embeddings and track progress."""
        mock_engine.return_value = "sqlite"
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_load.return_value = None  # No previous progress
        mock_save.return_value = True
        mock_clear.return_value = True
        
        # Create test embeddings
        embeddings = [(1, [0.1] * 10), (2, [0.2] * 10), (3, [0.3] * 10)]
        
        result = stream_embeddings_with_progress(
            run_id="test-run",
            updates=iter(embeddings),
            total_items=3,
            batch_size=2,
        )
        
        assert result == 3
        # Should have saved progress at least once
        assert mock_save.called
        # Should have cleared progress on completion
        assert mock_clear.called
    
    @patch("integration_coworker.persistence.streaming.load_streaming_progress")
    @patch("integration_coworker.persistence.streaming.save_streaming_progress")
    @patch("integration_coworker.persistence.streaming.clear_streaming_progress")
    @patch("integration_coworker.persistence.streaming.get_engine_type")
    @patch("integration_coworker.persistence.streaming.get_connection")
    def test_resumes_from_checkpoint(
        self, mock_get_conn, mock_engine, mock_clear, mock_save, mock_load
    ):
        """Should skip already processed items when resuming."""
        mock_engine.return_value = "sqlite"
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_save.return_value = True
        mock_clear.return_value = True
        
        # Simulate previous progress - already processed id=2
        mock_load.return_value = {
            "run_id": "test-run",
            "phase": "embeddings",
            "last_committed_id": 2,
            "total_items": 5,
            "started_at": "2025-01-01",
            "updated_at": "2025-01-01",
        }
        
        # Create test embeddings (some already processed)
        embeddings = [
            (1, [0.1] * 10),  # Already processed
            (2, [0.2] * 10),  # Already processed
            (3, [0.3] * 10),  # Should process
            (4, [0.4] * 10),  # Should process
            (5, [0.5] * 10),  # Should process
        ]
        
        result = stream_embeddings_with_progress(
            run_id="test-run",
            updates=iter(embeddings),
            total_items=5,
            batch_size=10,
        )
        
        # Should have only processed items 3, 4, 5
        assert result == 3
        
        # Verify only new items were updated (3 UPDATE calls)
        update_calls = [
            call for call in mock_cursor.execute.call_args_list
            if "UPDATE" in str(call)
        ]
        assert len(update_calls) == 3


# ---------------------------------------------------------------------------
# Tests: Error Handling
# ---------------------------------------------------------------------------

class TestProgressErrorHandling:
    """Tests for error handling in progress tracking."""
    
    @patch("integration_coworker.persistence.streaming.get_engine_type")
    @patch("integration_coworker.persistence.streaming.get_connection")
    def test_save_progress_handles_db_error(self, mock_get_conn, mock_engine):
        """save_streaming_progress should handle database errors gracefully."""
        mock_engine.return_value = "sqlite"
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("DB error")
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        
        result = save_streaming_progress("run-123", "embeddings", 50, 100)
        
        # Should return False and not crash
        assert result is False
        assert mock_conn.rollback.called
    
    @patch("integration_coworker.persistence.streaming.get_engine_type")
    @patch("integration_coworker.persistence.streaming.get_connection")
    def test_load_progress_handles_db_error(self, mock_get_conn, mock_engine):
        """load_streaming_progress should handle database errors gracefully."""
        mock_engine.return_value = "sqlite"
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("DB error")
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        
        result = load_streaming_progress("run-123", "embeddings")
        
        # Should return None and not crash
        assert result is None


# ---------------------------------------------------------------------------
# Tests: PostgreSQL-specific
# ---------------------------------------------------------------------------

class TestPostgresProgress:
    """Tests for PostgreSQL-specific progress tracking."""
    
    @patch("integration_coworker.persistence.streaming.get_engine_type")
    @patch("integration_coworker.persistence.streaming.get_connection")
    def test_postgres_uses_correct_schema(self, mock_get_conn, mock_engine):
        """PostgreSQL should use integration_gold schema."""
        mock_engine.return_value = "postgres"
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (
            "test-run", "embeddings", 50, 100, "2025-01-01", "2025-01-01"
        )
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        
        load_streaming_progress("test-run", "embeddings")
        
        # Should have used integration_gold schema
        call_args = mock_cursor.execute.call_args[0][0]
        assert "integration_gold.streaming_progress" in call_args


# ---------------------------------------------------------------------------
# Tests: stream_embedding_batch with run_id (V4 wiring)
# ---------------------------------------------------------------------------

class TestStreamEmbeddingBatchWithRunId:
    """Tests for stream_embedding_batch with run_id progress tracking."""
    
    @patch("integration_coworker.persistence.streaming.save_streaming_progress")
    @patch("integration_coworker.persistence.streaming.load_streaming_progress")
    @patch("integration_coworker.persistence.streaming.get_engine_type")
    @patch("integration_coworker.persistence.streaming.get_connection")
    def test_batch_with_run_id_saves_progress(
        self, mock_get_conn, mock_engine, mock_load, mock_save
    ):
        """stream_embedding_batch should save progress when run_id provided."""
        from integration_coworker.persistence.streaming import stream_embedding_batch
        
        mock_engine.return_value = "sqlite"
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_load.return_value = None  # No previous progress
        mock_save.return_value = True
        
        updates = [(1, [0.1, 0.2]), (2, [0.3, 0.4]), (3, [0.5, 0.6])]
        result = stream_embedding_batch(updates, run_id="test-run-123", total_items=10)
        
        assert result == 3
        # Should have loaded progress
        mock_load.assert_called_once_with("test-run-123", "embeddings")
        # Should have saved progress after commit
        mock_save.assert_called_once()
        call_args = mock_save.call_args
        assert call_args[0][0] == "test-run-123"
        assert call_args[0][1] == "embeddings"
        assert call_args[0][2] == 3  # last_id
        assert call_args[0][3] == 10  # total_items
    
    @patch("integration_coworker.persistence.streaming.save_streaming_progress")
    @patch("integration_coworker.persistence.streaming.load_streaming_progress")
    @patch("integration_coworker.persistence.streaming.get_engine_type")
    @patch("integration_coworker.persistence.streaming.get_connection")
    def test_batch_skips_already_processed(
        self, mock_get_conn, mock_engine, mock_load, mock_save
    ):
        """stream_embedding_batch should skip chunks <= last_committed_id."""
        from integration_coworker.persistence.streaming import stream_embedding_batch
        
        mock_engine.return_value = "sqlite"
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        mock_load.return_value = {"last_committed_id": 2}  # Already processed up to ID 2
        mock_save.return_value = True
        
        # Chunks 1, 2 should be skipped; only 3 processed
        updates = [(1, [0.1, 0.2]), (2, [0.3, 0.4]), (3, [0.5, 0.6])]
        result = stream_embedding_batch(updates, run_id="test-run-123")
        
        # Only 1 should be processed (chunk 3)
        assert result == 1
        # Should only have executed 1 UPDATE (not 3)
        assert mock_cursor.execute.call_count == 1
    
    @patch("integration_coworker.persistence.streaming.save_streaming_progress")
    @patch("integration_coworker.persistence.streaming.load_streaming_progress")
    @patch("integration_coworker.persistence.streaming.get_engine_type")
    @patch("integration_coworker.persistence.streaming.get_connection")
    def test_batch_without_run_id_no_progress(
        self, mock_get_conn, mock_engine, mock_load, mock_save
    ):
        """stream_embedding_batch without run_id should not track progress."""
        from integration_coworker.persistence.streaming import stream_embedding_batch
        
        mock_engine.return_value = "sqlite"
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        
        updates = [(1, [0.1, 0.2]), (2, [0.3, 0.4])]
        result = stream_embedding_batch(updates)  # No run_id
        
        assert result == 2
        # Should NOT have called progress functions
        mock_load.assert_not_called()
        mock_save.assert_not_called()
