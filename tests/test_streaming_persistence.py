"""
Tests for V3 Streaming Persistence functionality.

Tests cover:
1. streaming.py - stream_chunks_to_silver, stream_embedding_update, etc.
2. lazy_loader.py - iter_chunks, get_chunk_by_id, get_chunk_count, etc.
3. Integration with ingest_spec and embed_spec_chunks in streaming mode
"""
import os
import pytest
from unittest.mock import patch, MagicMock

# Set up test environment before imports
os.environ["USE_SQLITE"] = "true"
os.environ["USE_MOCK_LLM"] = "true"


class TestStreamingModule:
    """Tests for persistence/streaming.py functions."""
    
    def test_stream_chunks_to_silver_basic(self):
        """Test basic chunk streaming to database."""
        from integration_coworker.persistence.streaming import stream_chunks_to_silver
        from integration_coworker.persistence import db
        
        # Initialize schema for test
        db.init_schema()
        
        # Create test spec_document first
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO source_systems (code, display_name) 
            VALUES ('test', 'Test')
        """)
        cur.execute("SELECT id FROM source_systems WHERE code = 'test'")
        source_system_id = cur.fetchone()[0]
        
        cur.execute("""
            INSERT OR IGNORE INTO spec_documents 
                (source_system_id, uri, sha256, content_type)
            VALUES (?, 'test://spec', 'abc123', 'application/yaml')
        """, (source_system_id,))
        cur.execute("""
            SELECT id FROM spec_documents 
            WHERE uri = 'test://spec'
        """)
        spec_document_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        
        # Test streaming chunks
        test_chunks = [
            (0, "chunk content 0"),
            (1, "chunk content 1"),
            (2, "chunk content 2"),
        ]
        
        chunk_ids = stream_chunks_to_silver(
            iter(test_chunks),
            spec_document_id,
        )
        
        assert len(chunk_ids) == 3
        assert all(isinstance(id, int) for id in chunk_ids)
    
    def test_stream_embedding_update(self):
        """Test streaming embedding updates to database."""
        from integration_coworker.persistence.streaming import (
            stream_chunks_to_silver,
            stream_embedding_update,
        )
        from integration_coworker.persistence import db
        
        db.init_schema()
        
        # Set up test data
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO source_systems (code, display_name) 
            VALUES ('test_embed', 'Test Embed')
        """)
        cur.execute("SELECT id FROM source_systems WHERE code = 'test_embed'")
        source_system_id = cur.fetchone()[0]
        
        cur.execute("""
            INSERT OR IGNORE INTO spec_documents 
                (source_system_id, uri, sha256, content_type)
            VALUES (?, 'test://embed', 'def456', 'application/yaml')
        """, (source_system_id,))
        cur.execute("""
            SELECT id FROM spec_documents 
            WHERE uri = 'test://embed'
        """)
        spec_document_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        
        # Stream a chunk first
        test_chunks = [(0, "test chunk for embedding")]
        chunk_ids = stream_chunks_to_silver(iter(test_chunks), spec_document_id)
        
        assert len(chunk_ids) == 1
        chunk_id = chunk_ids[0]
        
        # Update with embedding
        test_embedding = [0.1] * 1536
        result = stream_embedding_update(chunk_id, test_embedding)
        
        assert result is True
        
        # Verify embedding was stored
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT embedding FROM spec_chunks WHERE id = ?", (chunk_id,))
        row = cur.fetchone()
        conn.close()
        
        assert row is not None
        import json
        stored_embedding = json.loads(row[0])
        assert len(stored_embedding) == 1536
    
    def test_get_chunk_ids_for_spec(self):
        """Test getting chunk IDs for a spec document."""
        from integration_coworker.persistence.streaming import (
            stream_chunks_to_silver,
            get_chunk_ids_for_spec,
        )
        from integration_coworker.persistence import db
        
        db.init_schema()
        
        # Set up test data
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO source_systems (code, display_name) 
            VALUES ('test_ids', 'Test IDs')
        """)
        cur.execute("SELECT id FROM source_systems WHERE code = 'test_ids'")
        source_system_id = cur.fetchone()[0]
        
        cur.execute("""
            INSERT OR IGNORE INTO spec_documents 
                (source_system_id, uri, sha256, content_type)
            VALUES (?, 'test://ids', 'ghi789', 'application/yaml')
        """, (source_system_id,))
        cur.execute("""
            SELECT id FROM spec_documents 
            WHERE uri = 'test://ids'
        """)
        spec_document_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        
        # Stream some chunks
        test_chunks = [(i, f"chunk {i}") for i in range(5)]
        stream_chunks_to_silver(iter(test_chunks), spec_document_id)
        
        # Get chunk IDs
        chunk_ids = get_chunk_ids_for_spec(spec_document_id)
        
        assert len(chunk_ids) == 5
        assert all(isinstance(id, int) for id in chunk_ids)


class TestLazyLoaderModule:
    """Tests for persistence/lazy_loader.py functions."""
    
    def test_iter_chunks(self):
        """Test lazy iteration over chunks."""
        from integration_coworker.persistence.streaming import stream_chunks_to_silver
        from integration_coworker.persistence.lazy_loader import iter_chunks
        from integration_coworker.persistence import db
        
        db.init_schema()
        
        # Set up test data
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO source_systems (code, display_name) 
            VALUES ('test_iter', 'Test Iter')
        """)
        cur.execute("SELECT id FROM source_systems WHERE code = 'test_iter'")
        source_system_id = cur.fetchone()[0]
        
        cur.execute("""
            INSERT OR IGNORE INTO spec_documents 
                (source_system_id, uri, sha256, content_type)
            VALUES (?, 'test://iter', 'jkl012', 'application/yaml')
        """, (source_system_id,))
        cur.execute("""
            SELECT id FROM spec_documents 
            WHERE uri = 'test://iter'
        """)
        spec_document_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        
        # Stream chunks
        test_chunks = [(i, f"iterable chunk {i}") for i in range(10)]
        stream_chunks_to_silver(iter(test_chunks), spec_document_id)
        
        # Iterate with small batch size to test pagination
        chunks = list(iter_chunks(spec_document_id, batch_size=3))
        
        assert len(chunks) == 10
        # Each chunk is (chunk_id, chunk_index, content, embedding)
        for chunk_id, chunk_index, content, embedding in chunks:
            assert isinstance(chunk_id, int)
            assert isinstance(chunk_index, int)
            assert f"iterable chunk {chunk_index}" in content
            assert embedding is None  # No embeddings yet
    
    def test_get_chunk_by_id(self):
        """Test loading a single chunk by ID."""
        from integration_coworker.persistence.streaming import stream_chunks_to_silver
        from integration_coworker.persistence.lazy_loader import get_chunk_by_id
        from integration_coworker.persistence import db
        
        db.init_schema()
        
        # Set up test data
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO source_systems (code, display_name) 
            VALUES ('test_byid', 'Test ById')
        """)
        cur.execute("SELECT id FROM source_systems WHERE code = 'test_byid'")
        source_system_id = cur.fetchone()[0]
        
        cur.execute("""
            INSERT OR IGNORE INTO spec_documents 
                (source_system_id, uri, sha256, content_type)
            VALUES (?, 'test://byid', 'mno345', 'application/yaml')
        """, (source_system_id,))
        cur.execute("""
            SELECT id FROM spec_documents 
            WHERE uri = 'test://byid'
        """)
        spec_document_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        
        # Stream a chunk
        test_chunks = [(0, "specific chunk content")]
        chunk_ids = stream_chunks_to_silver(iter(test_chunks), spec_document_id)
        
        # Load by ID
        chunk = get_chunk_by_id(chunk_ids[0])
        
        assert chunk is not None
        assert chunk["id"] == chunk_ids[0]
        assert chunk["chunk_index"] == 0
        assert "specific chunk content" in chunk["content"]
    
    def test_get_chunk_count(self):
        """Test getting chunk count for a spec document."""
        from integration_coworker.persistence.streaming import stream_chunks_to_silver
        from integration_coworker.persistence.lazy_loader import get_chunk_count
        from integration_coworker.persistence import db
        
        db.init_schema()
        
        # Set up test data
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO source_systems (code, display_name) 
            VALUES ('test_count', 'Test Count')
        """)
        cur.execute("SELECT id FROM source_systems WHERE code = 'test_count'")
        source_system_id = cur.fetchone()[0]
        
        cur.execute("""
            INSERT OR IGNORE INTO spec_documents 
                (source_system_id, uri, sha256, content_type)
            VALUES (?, 'test://count', 'pqr678', 'application/yaml')
        """, (source_system_id,))
        cur.execute("""
            SELECT id FROM spec_documents 
            WHERE uri = 'test://count'
        """)
        spec_document_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        
        # Stream chunks
        test_chunks = [(i, f"counted chunk {i}") for i in range(7)]
        stream_chunks_to_silver(iter(test_chunks), spec_document_id)
        
        # Get count
        count = get_chunk_count(spec_document_id)
        
        assert count == 7


class TestStreamingModeIntegration:
    """Integration tests for streaming mode with ingest_spec and embed_spec_chunks."""
    
    def test_ingest_spec_streaming_mode_forced(self, tmp_path):
        """Test ingest_spec when streaming is forced ON."""
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.graph.nodes.ingest_spec import ingest_spec
        from integration_coworker.persistence import db
        
        # Create a small test spec file
        spec_content = """
openapi: 3.0.0
info:
  title: Test API
  version: 1.0.0
paths:
  /test:
    get:
      summary: Test endpoint
"""
        spec_file = tmp_path / "test_spec.yaml"
        spec_file.write_text(spec_content)
        
        # Initialize DB
        db.init_schema()
        
        # Create state
        state = WorkflowState(
            source_refs=[],
            spec_refs=[str(spec_file)],
            task_description="Test task",
            provider_code="test_streaming",
            plan={},
        )
        
        # Force streaming mode ON
        with patch.dict(os.environ, {"STREAMING_PERSISTENCE": "true"}):
            from integration_coworker.config import reset_settings
            reset_settings()
            
            # Run ingest_spec
            result = ingest_spec(state)
        
        # Verify streaming mode behavior
        assert result.doc_chunks == []  # Should be empty in streaming mode
        assert result.chunk_count > 0  # Should have count
        assert len(result.spec_chunk_ids) > 0  # Should have IDs
        assert result.persisted_ids.get("chunks_streamed") is True
        
        # Reset settings
        from integration_coworker.config import reset_settings
        reset_settings()
    
    def test_ingest_spec_legacy_mode_forced(self, tmp_path):
        """Test ingest_spec when streaming is forced OFF."""
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.graph.nodes.ingest_spec import ingest_spec
        from integration_coworker.config import reset_settings
        
        # Create a test spec file
        spec_content = """
openapi: 3.0.0
info:
  title: Test API
  version: 1.0.0
paths:
  /test:
    get:
      summary: Test endpoint
"""
        spec_file = tmp_path / "test_spec.yaml"
        spec_file.write_text(spec_content)
        
        # Create state
        state = WorkflowState(
            source_refs=[],
            spec_refs=[str(spec_file)],
            task_description="Test task",
            provider_code="test_legacy",
            plan={},
        )
        
        # Force streaming mode OFF
        with patch.dict(os.environ, {"STREAMING_PERSISTENCE": "off"}):
            reset_settings()
            
            # Run ingest_spec
            result = ingest_spec(state)
        
        # Verify legacy mode behavior
        assert len(result.doc_chunks) > 0  # Should have chunks in memory
        assert result.persisted_ids.get("chunks_streamed") is not True
        
        reset_settings()
    
    def test_ingest_spec_auto_mode_small_spec(self, tmp_path):
        """Test that auto mode uses legacy for small specs."""
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.graph.nodes.ingest_spec import ingest_spec
        from integration_coworker.config import reset_settings
        
        # Create a small spec (< 500KB)
        spec_content = "openapi: 3.0.0\ninfo:\n  title: Small API\n  version: 1.0.0\n"
        spec_file = tmp_path / "small_spec.yaml"
        spec_file.write_text(spec_content)
        
        state = WorkflowState(
            source_refs=[],
            spec_refs=[str(spec_file)],
            task_description="Test task",
            provider_code="test_auto_small",
            plan={},
        )
        
        # Use auto mode (default)
        with patch.dict(os.environ, {"STREAMING_PERSISTENCE": "auto"}):
            reset_settings()
            result = ingest_spec(state)
        
        # Small spec should use legacy mode
        assert len(result.doc_chunks) > 0  # Has chunks in memory
        assert result.persisted_ids.get("chunks_streamed") is not True
        
        reset_settings()
    
    def test_ingest_spec_auto_mode_large_spec(self, tmp_path):
        """Test that auto mode uses streaming for large specs."""
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.graph.nodes.ingest_spec import ingest_spec
        from integration_coworker.persistence import db
        from integration_coworker.config import reset_settings
        
        # Create a large spec (> 500KB) - generate repetitive content
        base_path = """
  /endpoint{n}:
    get:
      summary: Test endpoint {n}
      responses:
        '200':
          description: Success
"""
        paths = "\n".join(base_path.format(n=i) for i in range(2000))  # ~600KB
        spec_content = f"""
openapi: 3.0.0
info:
  title: Large API
  version: 1.0.0
paths:{paths}
"""
        spec_file = tmp_path / "large_spec.yaml"
        spec_file.write_text(spec_content)
        
        db.init_schema()
        
        state = WorkflowState(
            source_refs=[],
            spec_refs=[str(spec_file)],
            task_description="Test task",
            provider_code="test_auto_large",
            plan={},
        )
        
        # Use auto mode (default) with lower threshold for test
        with patch.dict(os.environ, {
            "STREAMING_PERSISTENCE": "auto",
            "STREAMING_THRESHOLD_BYTES": "100000",  # 100KB threshold
        }):
            reset_settings()
            result = ingest_spec(state)
        
        # Large spec should use streaming mode
        assert result.doc_chunks == []  # Empty in streaming
        assert result.persisted_ids.get("chunks_streamed") is True
        
        reset_settings()
    
    def test_feature_flag_default_auto(self):
        """Test that streaming persistence default is 'auto'."""
        from integration_coworker.config import get_settings, reset_settings
        
        # Clear any env override
        with patch.dict(os.environ, {}, clear=False):
            if "STREAMING_PERSISTENCE" in os.environ:
                del os.environ["STREAMING_PERSISTENCE"]
            reset_settings()
            
            settings = get_settings()
            assert settings.streaming_persistence == "auto"
    
    def test_feature_flag_enabled(self):
        """Test that streaming persistence can be enabled via env var."""
        from integration_coworker.config import is_streaming_persistence_enabled, reset_settings
        
        with patch.dict(os.environ, {"STREAMING_PERSISTENCE": "true"}):
            reset_settings()
            assert is_streaming_persistence_enabled() is True
        
        # Cleanup
        reset_settings()
    
    def test_should_use_streaming_thresholds(self):
        """Test the should_use_streaming_for_spec function with thresholds."""
        from integration_coworker.config import should_use_streaming_for_spec, reset_settings
        
        with patch.dict(os.environ, {"STREAMING_PERSISTENCE": "auto"}):
            reset_settings()
            
            # Small spec - should not stream
            assert should_use_streaming_for_spec(100_000, 100) is False
            
            # Large spec by bytes - should stream
            assert should_use_streaming_for_spec(600_000, 100) is True
            
            # Large spec by chunks - should stream
            assert should_use_streaming_for_spec(100_000, 600) is True
        
        reset_settings()
