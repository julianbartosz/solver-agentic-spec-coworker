"""
Tests for cache_consistency module (V23-012 Production Fix).

Tests the cache-database consistency validation and repair functionality
that prevents slow fallback paths due to stale cache entries.
"""
import os
import sys
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock


class TestCacheConsistencyResult:
    """Tests for CacheConsistencyResult dataclass."""
    
    def test_default_values(self):
        """Test that defaults are set correctly."""
        from integration_coworker.persistence.cache_consistency import CacheConsistencyResult
        
        result = CacheConsistencyResult(is_consistent=True)
        
        assert result.is_consistent is True
        assert result.db_spec_document_count == 0
        assert result.db_endpoint_count == 0
        assert result.redis_available is False
        assert result.checkpoint_count == 0
        assert result.stale_checkpoint_count == 0
        assert result.issues == []
        assert result.provider_code is None
        assert isinstance(result.checked_at, datetime)
    
    def test_to_dict_serialization(self):
        """Test serialization to dict for logging/JSON output."""
        from integration_coworker.persistence.cache_consistency import CacheConsistencyResult
        
        result = CacheConsistencyResult(
            is_consistent=False,
            db_endpoint_count=100,
            redis_llm_key_count=50,
            issues=["Cache-DB mismatch"],
            provider_code="stripe",
        )
        
        d = result.to_dict()
        
        assert d["is_consistent"] is False
        assert d["db_endpoint_count"] == 100
        assert d["redis_llm_key_count"] == 50
        assert d["issues"] == ["Cache-DB mismatch"]
        assert d["provider_code"] == "stripe"
        assert "checked_at" in d


class TestRedisHelpers:
    """Tests for Redis helper functions."""
    
    def test_get_redis_client_connection_refused(self):
        """Test graceful handling when Redis connection refused."""
        from integration_coworker.persistence.cache_consistency import _get_redis_client
        
        # Create a mock redis module that raises on connection
        mock_redis_module = MagicMock()
        mock_redis_module.from_url.return_value.ping.side_effect = Exception("Connection refused")
        
        with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379"}):
            with patch.dict(sys.modules, {"redis": mock_redis_module}):
                # Force reimport to use mocked module
                from integration_coworker.persistence import cache_consistency
                # Manually call the function which will use the mocked redis
                client = cache_consistency._get_redis_client()
                
                # Should return None on connection error
                assert client is None
    
    def test_count_redis_keys_empty(self):
        """Test counting keys with no matches."""
        from integration_coworker.persistence.cache_consistency import _count_redis_keys
        
        mock_client = MagicMock()
        mock_client.scan.return_value = (0, [])
        
        count = _count_redis_keys(mock_client, "llm:*")
        
        assert count == 0
    
    def test_count_redis_keys_with_results(self):
        """Test counting keys with multiple scan batches."""
        from integration_coworker.persistence.cache_consistency import _count_redis_keys
        
        mock_client = MagicMock()
        # First scan returns 3 keys and cursor for next page
        # Second scan returns 2 keys and cursor 0 (end)
        mock_client.scan.side_effect = [
            (123, ["llm:1", "llm:2", "llm:3"]),
            (0, ["llm:4", "llm:5"]),
        ]
        
        count = _count_redis_keys(mock_client, "llm:*")
        
        assert count == 5
    
    def test_count_redis_keys_none_client(self):
        """Test that None client returns 0."""
        from integration_coworker.persistence.cache_consistency import _count_redis_keys
        
        count = _count_redis_keys(None, "llm:*")
        
        assert count == 0


class TestValidateCacheConsistency:
    """Tests for the main validate_cache_consistency function."""
    
    def test_consistent_state(self):
        """Test detection of consistent cache-database state."""
        from integration_coworker.persistence.cache_consistency import validate_cache_consistency
        
        with patch("integration_coworker.persistence.cache_consistency._get_redis_client") as mock_redis, \
             patch("integration_coworker.persistence.cache_consistency._get_db_counts") as mock_db_counts, \
             patch("integration_coworker.persistence.cache_consistency._count_stale_checkpoints") as mock_stale, \
             patch("integration_coworker.persistence.cache_consistency._count_redis_keys") as mock_count:
            
            # Setup: Redis has cache, database has matching data
            mock_redis.return_value = MagicMock()
            mock_count.return_value = 100
            mock_db_counts.return_value = {
                "spec_documents": 5,
                "endpoints": 150,
                "schemas": 50,
                "chunks": 1000,
                "checkpoints": 10,
                "checkpoint_blobs": 50,
            }
            mock_stale.return_value = 0
            
            result = validate_cache_consistency()
        
        assert result.is_consistent is True
        assert result.redis_available is True
        assert result.issues == []
    
    def test_detects_cache_db_mismatch(self):
        """Test detection of Redis cache with empty database."""
        from integration_coworker.persistence.cache_consistency import validate_cache_consistency
        
        with patch("integration_coworker.persistence.cache_consistency._get_redis_client") as mock_redis, \
             patch("integration_coworker.persistence.cache_consistency._get_db_counts") as mock_db_counts, \
             patch("integration_coworker.persistence.cache_consistency._count_stale_checkpoints") as mock_stale, \
             patch("integration_coworker.persistence.cache_consistency._count_redis_keys") as mock_count:
            
            # Setup: Redis has LLM cache but database is empty (truncated)
            mock_redis.return_value = MagicMock()
            mock_count.return_value = 100
            mock_db_counts.return_value = {
                "spec_documents": 0,
                "endpoints": 0,  # Empty!
                "schemas": 0,
                "chunks": 0,
                "checkpoints": 50,
                "checkpoint_blobs": 100,
            }
            mock_stale.return_value = 0
            
            result = validate_cache_consistency()
        
        assert result.is_consistent is False
        assert len(result.issues) > 0
        assert any("mismatch" in issue.lower() for issue in result.issues)
    
    def test_detects_stale_checkpoints(self):
        """Test detection of stale checkpoints with cache_hit=True."""
        from integration_coworker.persistence.cache_consistency import validate_cache_consistency
        
        with patch("integration_coworker.persistence.cache_consistency._get_redis_client") as mock_redis, \
             patch("integration_coworker.persistence.cache_consistency._get_db_counts") as mock_db_counts, \
             patch("integration_coworker.persistence.cache_consistency._count_stale_checkpoints") as mock_stale, \
             patch("integration_coworker.persistence.cache_consistency._count_redis_keys") as mock_count:
            
            mock_redis.return_value = MagicMock()
            mock_count.return_value = 0
            mock_db_counts.return_value = {
                "spec_documents": 5,
                "endpoints": 100,
                "schemas": 50,
                "chunks": 1000,
                "checkpoints": 100,
                "checkpoint_blobs": 500,
            }
            # Found stale checkpoints pointing to deleted spec_documents
            mock_stale.return_value = 10
            
            result = validate_cache_consistency()
        
        assert result.is_consistent is False
        assert result.stale_checkpoint_count == 10
        assert any("stale checkpoint" in issue.lower() for issue in result.issues)
    
    def test_detects_suspicious_checkpoint_state(self):
        """Test detection of checkpoints with empty database."""
        from integration_coworker.persistence.cache_consistency import validate_cache_consistency
        
        with patch("integration_coworker.persistence.cache_consistency._get_redis_client") as mock_redis, \
             patch("integration_coworker.persistence.cache_consistency._get_db_counts") as mock_db_counts, \
             patch("integration_coworker.persistence.cache_consistency._count_stale_checkpoints") as mock_stale, \
             patch("integration_coworker.persistence.cache_consistency._count_redis_keys") as mock_count:
            
            mock_redis.return_value = None  # Redis not available
            mock_count.return_value = 0
            mock_db_counts.return_value = {
                "spec_documents": 0,  # Empty!
                "endpoints": 0,
                "schemas": 0,
                "chunks": 0,
                "checkpoints": 100,  # But many checkpoints exist!
                "checkpoint_blobs": 500,
            }
            mock_stale.return_value = 0
            
            result = validate_cache_consistency()
        
        assert result.is_consistent is False
        assert any("suspicious" in issue.lower() for issue in result.issues)


class TestValidateCacheHitClaim:
    """Tests for cache hit claim validation."""
    
    def test_rejects_none_spec_document_id(self):
        """Test that None spec_document_id fails validation."""
        from integration_coworker.persistence.cache_consistency import validate_cache_hit_claim
        
        result = validate_cache_hit_claim(None, "stripe")
        
        assert result is False
    
    def test_validates_existing_spec_document(self):
        """Test that valid spec_document_id passes validation."""
        from integration_coworker.persistence.cache_consistency import validate_cache_hit_claim
        
        # Mock the entire db module path used by the function
        with patch("integration_coworker.persistence.db") as mock_db, \
             patch("integration_coworker.persistence.sql_helpers.get_engine_type") as mock_engine:
            
            mock_engine.return_value = "postgres"
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            # V27-003: Support context manager protocol
            mock_db.get_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db.get_connection.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.cursor.return_value = mock_cur
            
            # First query: spec_document exists
            # Second query: endpoints exist for this spec_document
            mock_cur.fetchone.side_effect = [(1,), (50,)]
            
            result = validate_cache_hit_claim(123, "stripe")
        
        assert result is True
    
    def test_rejects_missing_spec_document(self):
        """Test that missing spec_document_id fails validation."""
        from integration_coworker.persistence.cache_consistency import validate_cache_hit_claim
        
        with patch("integration_coworker.persistence.db") as mock_db, \
             patch("integration_coworker.persistence.sql_helpers.get_engine_type") as mock_engine:
            
            mock_engine.return_value = "postgres"
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            # V27-003: Support context manager protocol
            mock_db.get_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db.get_connection.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.cursor.return_value = mock_cur
            
            # spec_document does not exist
            mock_cur.fetchone.return_value = (0,)
            
            result = validate_cache_hit_claim(999, "stripe")
        
        assert result is False
    
    def test_rejects_partial_cache(self):
        """Test that spec_document with 0 endpoints fails validation."""
        from integration_coworker.persistence.cache_consistency import validate_cache_hit_claim
        
        with patch("integration_coworker.persistence.db") as mock_db, \
             patch("integration_coworker.persistence.sql_helpers.get_engine_type") as mock_engine:
            
            mock_engine.return_value = "postgres"
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            # V27-003: Support context manager protocol
            mock_db.get_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db.get_connection.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.cursor.return_value = mock_cur
            
            # spec_document exists but has no endpoints
            mock_cur.fetchone.side_effect = [(1,), (0,)]
            
            result = validate_cache_hit_claim(123, "stripe")
        
        assert result is False


class TestClearStaleCache:
    """Tests for cache clearing functionality."""
    
    def test_clears_redis_keys(self):
        """Test Redis key clearing."""
        from integration_coworker.persistence.cache_consistency import clear_stale_cache
        
        with patch("integration_coworker.persistence.cache_consistency._get_redis_client") as mock_redis_fn, \
             patch("integration_coworker.persistence.db") as mock_db, \
             patch("integration_coworker.persistence.sql_helpers.get_engine_type") as mock_engine:
            
            mock_client = MagicMock()
            mock_redis_fn.return_value = mock_client
            mock_client.scan_iter.side_effect = [
                ["llm:1", "llm:2"],  # LLM keys
                ["emb:1"],  # Embedding keys
            ]
            
            mock_engine.return_value = "postgres"
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            # V27-003: Support context manager protocol
            mock_db.get_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db.get_connection.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.cursor.return_value = mock_cur
            mock_cur.rowcount = 5
            
            result = clear_stale_cache(clear_redis=True, clear_checkpoints=True)
        
        assert result["redis_llm_keys"] == 2
        assert result["redis_embedding_keys"] == 1
        mock_client.delete.assert_called()
    
    def test_clears_stale_checkpoints(self):
        """Test stale checkpoint deletion."""
        from integration_coworker.persistence.cache_consistency import clear_stale_cache
        
        with patch("integration_coworker.persistence.cache_consistency._get_redis_client") as mock_redis_fn, \
             patch("integration_coworker.persistence.db") as mock_db, \
             patch("integration_coworker.persistence.sql_helpers.get_engine_type") as mock_engine:
            
            mock_redis_fn.return_value = None  # Redis not available
            
            mock_engine.return_value = "postgres"
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            # V27-003: Support context manager protocol
            mock_db.get_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_db.get_connection.return_value.__exit__ = MagicMock(return_value=False)
            mock_conn.cursor.return_value = mock_cur
            mock_cur.rowcount = 10
            
            result = clear_stale_cache(clear_redis=False, clear_checkpoints=True)
        
        assert result["stale_checkpoints"] == 10


class TestEnsureCacheConsistency:
    """Tests for the main entry point with auto-repair."""
    
    def test_auto_repairs_inconsistency(self):
        """Test that auto_repair triggers clearing when inconsistent."""
        from integration_coworker.persistence.cache_consistency import (
            ensure_cache_consistency, CacheConsistencyResult
        )
        
        with patch("integration_coworker.persistence.cache_consistency.validate_cache_consistency") as mock_validate, \
             patch("integration_coworker.persistence.cache_consistency.clear_stale_cache") as mock_clear:
            
            # First call: inconsistent
            # Second call (after repair): consistent
            mock_validate.side_effect = [
                CacheConsistencyResult(
                    is_consistent=False,
                    issues=["Cache-DB mismatch"],
                ),
                CacheConsistencyResult(is_consistent=True),
            ]
            mock_clear.return_value = {
                "redis_llm_keys": 10,
                "redis_embedding_keys": 5,
                "stale_checkpoints": 3,
            }
            
            result = ensure_cache_consistency(auto_repair=True)
        
        assert result.is_consistent is True
        mock_clear.assert_called_once()
    
    def test_skips_repair_when_consistent(self):
        """Test that auto_repair skips clearing when already consistent."""
        from integration_coworker.persistence.cache_consistency import (
            ensure_cache_consistency, CacheConsistencyResult
        )
        
        with patch("integration_coworker.persistence.cache_consistency.validate_cache_consistency") as mock_validate, \
             patch("integration_coworker.persistence.cache_consistency.clear_stale_cache") as mock_clear:
            
            mock_validate.return_value = CacheConsistencyResult(is_consistent=True)
            
            result = ensure_cache_consistency(auto_repair=True)
        
        assert result.is_consistent is True
        mock_clear.assert_not_called()


class TestPreRunCacheCheck:
    """Tests for the quick pre-run check function."""
    
    def test_returns_true_when_consistent(self):
        """Test that consistent state returns True."""
        from integration_coworker.persistence.cache_consistency import (
            pre_run_cache_check, CacheConsistencyResult
        )
        
        with patch("integration_coworker.persistence.cache_consistency.ensure_cache_consistency") as mock_ensure:
            mock_ensure.return_value = CacheConsistencyResult(is_consistent=True)
            
            result = pre_run_cache_check("stripe")
        
        assert result is True
    
    def test_returns_false_when_strict_and_inconsistent(self):
        """Test that strict mode returns False on inconsistency."""
        from integration_coworker.persistence.cache_consistency import (
            pre_run_cache_check, CacheConsistencyResult
        )
        
        with patch("integration_coworker.persistence.cache_consistency.ensure_cache_consistency") as mock_ensure, \
             patch("integration_coworker.persistence.cache_consistency.CACHE_CONSISTENCY_STRICT", True):
            
            mock_ensure.return_value = CacheConsistencyResult(
                is_consistent=False,
                issues=["Unresolved issue"],
            )
            
            # Temporarily set CACHE_CONSISTENCY_STRICT
            from integration_coworker.persistence import cache_consistency
            original_strict = cache_consistency.CACHE_CONSISTENCY_STRICT
            cache_consistency.CACHE_CONSISTENCY_STRICT = True
            
            try:
                result = pre_run_cache_check("stripe")
            finally:
                cache_consistency.CACHE_CONSISTENCY_STRICT = original_strict
        
        assert result is False


class TestIntegrationWithHydration:
    """Integration tests for hydration cache validation."""
    
    def test_hydration_uses_cache_validator(self):
        """Test that _hydrate_from_cache uses validate_cache_hit_claim."""
        from integration_coworker.graph.nodes.build_silver_api_model import _hydrate_from_cache
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.domain.models import SpecDocument
        
        # Create a SpecDocument with all required fields
        spec_doc = SpecDocument(
            id=999,
            source_system_id=1,
            version="1.0",
            uri="http://example.com/spec.json",
            content_type="application/json",
            sha256="abc123",
            content="{}",
        )
        
        state = WorkflowState(
            run_id="test-run",
            source_refs=["test://source"],
            spec_refs=["test://spec"],
            task_description="Test task",
            cache_hit=True,
            spec_documents=[spec_doc],
            provider_code="stripe",
        )
        
        # Patch at the persistence module level since _hydrate_from_cache imports locally
        with patch("integration_coworker.persistence.cache_consistency.validate_cache_hit_claim") as mock_validate:
            mock_validate.return_value = False  # Simulate stale cache
            
            result = _hydrate_from_cache(state)
        
        # Should return False because cache validation failed
        assert result is False
        # cache_hit should be cleared
        assert state.cache_hit is False
        mock_validate.assert_called_once_with(999, "stripe")
