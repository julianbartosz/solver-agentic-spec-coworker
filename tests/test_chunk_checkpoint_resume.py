"""
Tests for chunk streaming checkpoint/resume functionality.

V4 Production Hardening: Verifies progress tracking and crash recovery.
Per docs/INGESTION_PROD_HARDENING_PLAN.md AT-4
"""

import pytest
from unittest.mock import patch, MagicMock
import os

from integration_coworker.persistence.streaming import (
    stream_chunks_to_silver_with_progress,
    save_streaming_progress,
    load_streaming_progress,
    clear_streaming_progress,
    init_streaming_progress_table,
)
from integration_coworker.persistence import db


@pytest.fixture(autouse=True)
def sqlite_db(tmp_path):
    """Use a temporary SQLite database for tests."""
    db_path = tmp_path / "test.db"
    os.environ["USE_SQLITE"] = "true"
    os.environ["SQLITE_PATH"] = str(db_path)
    
    # Reset DB module state
    from integration_coworker.persistence import db as db_module
    db_module._connection = None
    db_module._engine = None
    
    # Initialize schema
    db.init_schema()
    
    yield
    
    # Cleanup
    del os.environ["USE_SQLITE"]
    del os.environ["SQLITE_PATH"]
    db_module._connection = None
    db_module._engine = None


@pytest.fixture
def sample_chunks():
    """Generate sample chunks for testing."""
    return [
        (0, "chunk content 0"),
        (1, "chunk content 1"),
        (2, "chunk content 2"),
        (3, "chunk content 3"),
        (4, "chunk content 4"),
    ]


@pytest.fixture
def spec_document_id(sqlite_db):
    """Create a spec_document for testing."""
    # Create source_system first
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO source_systems (code, display_name) VALUES ('test', 'Test Provider')")
    cur.execute("SELECT id FROM source_systems WHERE code = 'test'")
    source_system_id = cur.fetchone()[0]
    
    # Create spec_document
    cur.execute("""
        INSERT OR IGNORE INTO spec_documents (source_system_id, uri, sha256, content_type)
        VALUES (?, 'test://spec.json', 'abc123hash', 'application/json')
    """, (source_system_id,))
    cur.execute("SELECT id FROM spec_documents WHERE uri = 'test://spec.json'")
    spec_doc_id = cur.fetchone()[0]
    
    conn.commit()
    conn.close()
    return spec_doc_id


class TestProgressTrackingBasics:
    """Test progress tracking save/load/clear operations."""
    
    def test_save_and_load_progress(self, sqlite_db):
        """Verify progress can be saved and loaded."""
        init_streaming_progress_table()
        
        save_streaming_progress("run-123", "chunks:1", 50, 100)
        
        progress = load_streaming_progress("run-123", "chunks:1")
        
        assert progress is not None
        assert progress["run_id"] == "run-123"
        assert progress["phase"] == "chunks:1"
        assert progress["last_committed_id"] == 50
        assert progress["total_items"] == 100
    
    def test_load_nonexistent_progress(self, sqlite_db):
        """Verify loading non-existent progress returns None."""
        init_streaming_progress_table()
        
        progress = load_streaming_progress("nonexistent", "phase")
        assert progress is None
    
    def test_clear_progress(self, sqlite_db):
        """Verify progress can be cleared."""
        init_streaming_progress_table()
        
        save_streaming_progress("run-456", "chunks:2", 25, 50)
        assert load_streaming_progress("run-456", "chunks:2") is not None
        
        clear_streaming_progress("run-456", "chunks:2")
        assert load_streaming_progress("run-456", "chunks:2") is None
    
    def test_update_existing_progress(self, sqlite_db):
        """Verify progress can be updated."""
        init_streaming_progress_table()
        
        save_streaming_progress("run-789", "chunks:3", 10, 100)
        save_streaming_progress("run-789", "chunks:3", 20, 100)
        
        progress = load_streaming_progress("run-789", "chunks:3")
        assert progress["last_committed_id"] == 20  # Updated


class TestChunkStreamingWithProgress:
    """Test stream_chunks_to_silver_with_progress function."""
    
    def test_streams_all_chunks(self, sqlite_db, spec_document_id, sample_chunks):
        """Verify all chunks are streamed successfully."""
        chunk_ids = stream_chunks_to_silver_with_progress(
            iter(sample_chunks),
            spec_document_id,
            run_id="test-run-1",
            total_chunks=5,
            batch_size=2,  # Small batch for testing
        )
        
        assert len(chunk_ids) == 5
        
        # Verify chunks in DB
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM spec_chunks WHERE spec_document_id = ?", (spec_document_id,))
        count = cur.fetchone()[0]
        conn.close()
        
        assert count == 5
    
    def test_progress_cleared_on_completion(self, sqlite_db, spec_document_id, sample_chunks):
        """Verify progress checkpoint is cleared after successful completion."""
        init_streaming_progress_table()
        
        stream_chunks_to_silver_with_progress(
            iter(sample_chunks),
            spec_document_id,
            run_id="test-run-2",
            total_chunks=5,
        )
        
        # Progress should be cleared after completion
        progress = load_streaming_progress("test-run-2", f"chunks:{spec_document_id}")
        assert progress is None
    
    def test_resume_skips_persisted_chunks(self, sqlite_db, spec_document_id):
        """Verify resume skips already-persisted chunks."""
        init_streaming_progress_table()
        run_id = "test-run-3"
        phase = f"chunks:{spec_document_id}"
        
        # First run: stream first 3 chunks, then simulate interruption
        first_batch = [
            (0, "chunk 0"),
            (1, "chunk 1"),
            (2, "chunk 2"),
        ]
        chunk_ids_1 = stream_chunks_to_silver_with_progress(
            iter(first_batch),
            spec_document_id,
            run_id=run_id,
            total_chunks=5,
            batch_size=10,  # Single batch, no intermediate checkpoint
        )
        
        # Manually save progress to simulate crash after commit
        save_streaming_progress(run_id, phase, 2, 5)
        
        # Second run: stream all 5 chunks (should skip first 3)
        all_chunks = [
            (0, "chunk 0"),
            (1, "chunk 1"),
            (2, "chunk 2"),
            (3, "chunk 3"),
            (4, "chunk 4"),
        ]
        chunk_ids_2 = stream_chunks_to_silver_with_progress(
            iter(all_chunks),
            spec_document_id,
            run_id=run_id,
            total_chunks=5,
        )
        
        # Should only return IDs for newly written chunks (3 and 4)
        assert len(chunk_ids_2) == 2
        
        # Verify all 5 chunks in DB
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM spec_chunks WHERE spec_document_id = ?", (spec_document_id,))
        count = cur.fetchone()[0]
        conn.close()
        
        assert count == 5
    
    def test_idempotent_on_restart(self, sqlite_db, spec_document_id, sample_chunks):
        """Verify restart doesn't create duplicate chunks."""
        # Stream chunks twice
        stream_chunks_to_silver_with_progress(
            iter(sample_chunks),
            spec_document_id,
            batch_size=10,
        )
        
        # Second run without run_id (no progress tracking)
        stream_chunks_to_silver_with_progress(
            iter(sample_chunks),
            spec_document_id,
            batch_size=10,
        )
        
        # Should still have exactly 5 chunks (no duplicates)
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM spec_chunks WHERE spec_document_id = ?", (spec_document_id,))
        count = cur.fetchone()[0]
        conn.close()
        
        assert count == 5


class TestCancellationDuringStreaming:
    """Test cancellation behavior during chunk streaming."""
    
    @patch('integration_coworker.shutdown.is_shutdown_requested')
    def test_cancellation_commits_partial(self, mock_shutdown, sqlite_db, spec_document_id):
        """Verify cancellation commits partial batch and saves progress."""
        init_streaming_progress_table()
        
        # Return False for first check, then True to simulate cancellation after first batch
        call_count = [0]
        def shutdown_side_effect():
            call_count[0] += 1
            # First call returns False, subsequent calls return True
            return call_count[0] > 1
        
        mock_shutdown.side_effect = shutdown_side_effect
        
        chunks = [
            (0, "chunk 0"),
            (1, "chunk 1"),
            (2, "chunk 2"),
            (3, "chunk 3"),
            (4, "chunk 4"),
        ]
        
        chunk_ids = stream_chunks_to_silver_with_progress(
            iter(chunks),
            spec_document_id,
            run_id="cancel-test",
            total_chunks=5,
            batch_size=2,  # Cancellation checked after each batch
        )
        
        # First batch (2 chunks) committed, then cancellation triggered
        # The second batch would also be partially written before the check
        assert len(chunk_ids) == 2 or len(chunk_ids) == 4  # Depends on exact timing
        
        # Progress should be saved (not cleared due to cancellation)
        phase = f"chunks:{spec_document_id}"
        progress = load_streaming_progress("cancel-test", phase)
        assert progress is not None
        # Either first batch endpoint (index=1) or second batch endpoint (index=3)
        assert progress["last_committed_id"] in [1, 3]
    
    def test_no_cancellation_clears_progress(self, sqlite_db, spec_document_id):
        """Verify progress is cleared when NOT cancelled (successful completion)."""
        init_streaming_progress_table()
        
        chunks = [
            (0, "chunk 0"),
            (1, "chunk 1"),
            (2, "chunk 2"),
        ]
        
        stream_chunks_to_silver_with_progress(
            iter(chunks),
            spec_document_id,
            run_id="complete-test",
            total_chunks=3,
        )
        
        # Progress should be cleared after successful completion
        phase = f"chunks:{spec_document_id}"
        progress = load_streaming_progress("complete-test", phase)
        assert progress is None
