"""
Benchmark tests for bulk persistence operations.

V22-007: Measures before/after performance of persist nodes.

Run: python -m pytest tests/persistence/test_bulk_benchmark.py -v -s
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional
from unittest.mock import MagicMock, patch
import pytest

# Skip if not in integration test mode
pytestmark = pytest.mark.skipif(
    os.environ.get("INTEGRATION_TESTS") != "1",
    reason="Set INTEGRATION_TESTS=1 to run persistence benchmarks"
)


@dataclass
class FakeSpecChunk:
    """Fake spec chunk for benchmarking."""
    chunk_index: int
    content: str
    embedding: Optional[List[float]] = None
    spec_document_id: Optional[int] = None
    id: Optional[int] = None


class TestBulkInsertBenchmark:
    """Benchmark tests for bulk insert operations."""
    
    @pytest.fixture
    def sqlite_conn(self, tmp_path):
        """Create a temporary SQLite database."""
        import sqlite3
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE spec_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                spec_document_id INTEGER,
                chunk_index INTEGER,
                content TEXT,
                embedding TEXT,
                UNIQUE(spec_document_id, chunk_index)
            )
        """)
        conn.commit()
        yield conn
        conn.close()
    
    def test_row_by_row_insert_baseline(self, sqlite_conn):
        """Measure baseline performance of row-by-row inserts."""
        # Generate test data
        num_rows = 1000
        chunks = [
            (1, i, f"Content for chunk {i}", json.dumps([0.1] * 384))
            for i in range(num_rows)
        ]
        
        # Time row-by-row inserts
        start = time.perf_counter()
        cur = sqlite_conn.cursor()
        for chunk in chunks:
            cur.execute("""
                INSERT OR IGNORE INTO spec_chunks 
                (spec_document_id, chunk_index, content, embedding)
                VALUES (?, ?, ?, ?)
            """, chunk)
        sqlite_conn.commit()
        elapsed = time.perf_counter() - start
        
        print(f"\n[Baseline] Row-by-row INSERT: {num_rows} rows in {elapsed*1000:.1f}ms")
        print(f"           Rate: {num_rows/elapsed:.0f} rows/sec")
        
        # Verify all rows inserted
        cur.execute("SELECT COUNT(*) FROM spec_chunks")
        assert cur.fetchone()[0] == num_rows
    
    def test_bulk_insert_improvement(self, sqlite_conn):
        """Measure improved performance with bulk inserts."""
        from integration_coworker.persistence.bulk import write_rows_insert
        
        # Patch get_engine_type to return sqlite
        with patch('integration_coworker.persistence.bulk.get_engine_type', return_value='sqlite'):
            # Generate test data
            num_rows = 1000
            chunks = [
                (1, i, f"Content for chunk {i}", json.dumps([0.1] * 384))
                for i in range(num_rows)
            ]
            
            # Time bulk insert
            start = time.perf_counter()
            write_rows_insert(
                sqlite_conn,
                "spec_chunks",
                ["spec_document_id", "chunk_index", "content", "embedding"],
                chunks,
                on_conflict="ignore"
            )
            elapsed = time.perf_counter() - start
            
            print(f"\n[Bulk] Multi-row INSERT: {num_rows} rows in {elapsed*1000:.1f}ms")
            print(f"       Rate: {num_rows/elapsed:.0f} rows/sec")
            
            # Verify all rows inserted
            cur = sqlite_conn.cursor()
            cur.execute("SELECT COUNT(*) FROM spec_chunks")
            assert cur.fetchone()[0] == num_rows
    
    def test_improvement_ratio(self, sqlite_conn, tmp_path):
        """Compare row-by-row vs bulk and assert improvement."""
        from integration_coworker.persistence.bulk import write_rows_insert
        
        num_rows = 500
        
        # Create test data
        chunks = [
            (1, i, f"Content for chunk {i}", json.dumps([0.1] * 384))
            for i in range(num_rows)
        ]
        
        # Measure row-by-row
        start = time.perf_counter()
        cur = sqlite_conn.cursor()
        for chunk in chunks:
            cur.execute("""
                INSERT OR IGNORE INTO spec_chunks 
                (spec_document_id, chunk_index, content, embedding)
                VALUES (?, ?, ?, ?)
            """, chunk)
        sqlite_conn.commit()
        row_by_row_time = time.perf_counter() - start
        
        # Clear table
        sqlite_conn.execute("DELETE FROM spec_chunks")
        sqlite_conn.commit()
        
        # Measure bulk
        with patch('integration_coworker.persistence.bulk.get_engine_type', return_value='sqlite'):
            start = time.perf_counter()
            write_rows_insert(
                sqlite_conn,
                "spec_chunks",
                ["spec_document_id", "chunk_index", "content", "embedding"],
                chunks,
                on_conflict="ignore"
            )
            bulk_time = time.perf_counter() - start
        
        improvement = row_by_row_time / bulk_time
        
        print(f"\n[Comparison] {num_rows} rows:")
        print(f"  Row-by-row: {row_by_row_time*1000:.1f}ms")
        print(f"  Bulk:       {bulk_time*1000:.1f}ms")
        print(f"  Improvement: {improvement:.1f}x faster")
        
        # SQLite uses executemany which is already efficient
        # Real gains are in Postgres with COPY (10-50x)
        # For SQLite, we just verify bulk doesn't regress
        assert improvement >= 1.0, f"Bulk should not be slower than row-by-row"


class TestPersistNodeDuration:
    """Test p95 duration targets for persist nodes."""
    
    @pytest.fixture
    def mock_state(self):
        """Create a mock workflow state with test data."""
        state = MagicMock()
        state.options = MagicMock(dry_run=True)
        state.persisted_ids = {}
        state.completed_steps = []
        state.source_system = None
        state.spec_documents = []
        state.spec_sections = []
        state.endpoints = []
        state.schemas = []
        state.schema_fields = []
        state.endpoint_parameters = []
        state.entities = []
        state.relationships = []
        state.events = []
        state.file_specs = []
        state.file_fields = []
        state.spec_chunk_embeddings = [
            FakeSpecChunk(i, f"Content {i}", [0.1] * 384)
            for i in range(100)
        ]
        state.provider_code = "test_provider"
        state.repo_root = "/tmp/test"
        state.plan = {}
        state.chunk_count = 100
        state.embedding_count = 100
        return state
    
    def test_persist_silver_dry_run_fast(self, mock_state):
        """Dry run should be very fast (no DB writes)."""
        from integration_coworker.graph.nodes.persist_silver_checkpoint import persist_silver_checkpoint
        
        times = []
        for _ in range(10):
            start = time.perf_counter()
            persist_silver_checkpoint(mock_state)
            elapsed = time.perf_counter() - start
            times.append(elapsed)
            mock_state.persisted_ids = {}  # Reset
            mock_state.completed_steps = []
        
        p95 = sorted(times)[int(len(times) * 0.95)]
        print(f"\n[persist_silver_checkpoint dry_run] p95: {p95*1000:.1f}ms")
        
        # Dry run should be < 10ms
        assert p95 < 0.010, f"Dry run too slow: {p95*1000:.1f}ms"


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    pytest.main([__file__, "-v", "-s"])
