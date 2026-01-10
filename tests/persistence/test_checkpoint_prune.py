"""Tests for checkpoint pruning.

Production Readiness v4 - P1-3:
Tests retention policy and concurrency safety.
"""

import pytest
from unittest.mock import patch, MagicMock

from integration_coworker.persistence.checkpoints import (
    prune_checkpoints,
    get_checkpoint_stats,
)


class TestPruneCheckpoints:
    """Test prune_checkpoints function."""

    def test_prune_returns_list(self):
        """Test prune returns list of deleted run_ids."""
        with patch('integration_coworker.persistence.checkpoints.get_engine_type') as mock_engine:
            mock_engine.return_value = "sqlite"
            with patch('integration_coworker.persistence.checkpoints.get_connection') as mock_conn:
                mock_cursor = MagicMock()
                mock_cursor.fetchall.return_value = [("run-1",), ("run-2",)]
                mock_conn.return_value.cursor.return_value = mock_cursor
                result = prune_checkpoints(retention_days=7, retention_count=10)
                assert isinstance(result, list)

    def test_prune_respects_retention_days(self):
        """Test prune uses retention_days parameter."""
        with patch('integration_coworker.persistence.checkpoints.get_engine_type') as mock_engine:
            mock_engine.return_value = "sqlite"
            with patch('integration_coworker.persistence.checkpoints.get_connection') as mock_conn:
                mock_cursor = MagicMock()
                mock_cursor.fetchall.return_value = []
                mock_conn.return_value.cursor.return_value = mock_cursor
                prune_checkpoints(retention_days=30, retention_count=5)
                call_args_list = mock_cursor.execute.call_args_list
                delete_call = call_args_list[0]
                assert 30 in delete_call[0][1]


class TestPostgresPrune:
    """Test PostgreSQL-specific pruning."""

    def test_postgres_uses_advisory_lock(self):
        """Test PostgreSQL pruning uses advisory lock."""
        with patch('integration_coworker.persistence.checkpoints.get_engine_type') as mock_engine:
            mock_engine.return_value = "postgres"
            with patch('integration_coworker.persistence.postgres.get_connection') as mock_pg_conn:
                mock_cursor = MagicMock()
                mock_cursor.fetchone.return_value = (True,)
                mock_cursor.fetchall.return_value = []
                mock_conn_obj = MagicMock()
                mock_conn_obj.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
                mock_conn_obj.cursor.return_value.__exit__ = MagicMock()
                mock_pg_conn.return_value.__enter__ = MagicMock(return_value=mock_conn_obj)
                mock_pg_conn.return_value.__exit__ = MagicMock()
                prune_checkpoints()
                calls = mock_cursor.execute.call_args_list
                lock_calls = [c for c in calls if 'pg_try_advisory_lock' in str(c)]
                assert len(lock_calls) >= 1

    def test_postgres_skips_if_lock_not_acquired(self):
        """Test PostgreSQL skips prune if lock not acquired."""
        with patch('integration_coworker.persistence.checkpoints.get_engine_type') as mock_engine:
            mock_engine.return_value = "postgres"
            with patch('integration_coworker.persistence.postgres.get_connection') as mock_pg_conn:
                mock_cursor = MagicMock()
                mock_cursor.fetchone.return_value = (False,)
                mock_conn_obj = MagicMock()
                mock_conn_obj.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
                mock_conn_obj.cursor.return_value.__exit__ = MagicMock()
                mock_pg_conn.return_value.__enter__ = MagicMock(return_value=mock_conn_obj)
                mock_pg_conn.return_value.__exit__ = MagicMock()
                result = prune_checkpoints()
                assert result == []


class TestSQLitePrune:
    """Test SQLite-specific pruning."""

    def test_sqlite_prune_syntax(self):
        """Test SQLite uses correct SQL syntax."""
        with patch('integration_coworker.persistence.checkpoints.get_engine_type') as mock_engine:
            mock_engine.return_value = "sqlite"
            with patch('integration_coworker.persistence.checkpoints.get_connection') as mock_conn:
                mock_cursor = MagicMock()
                mock_cursor.fetchall.return_value = []
                mock_conn.return_value.cursor.return_value = mock_cursor
                prune_checkpoints(retention_days=7, retention_count=10)
                call_args = mock_cursor.execute.call_args_list[0]
                sql = call_args[0][0]
                assert "julianday" in sql.lower()


class TestGetCheckpointStats:
    """Test get_checkpoint_stats function."""

    def test_stats_returns_dict(self):
        """Test stats returns dict with expected keys."""
        with patch('integration_coworker.persistence.checkpoints.get_engine_type') as mock_engine:
            mock_engine.return_value = "sqlite"
            with patch('integration_coworker.persistence.checkpoints.get_connection') as mock_conn:
                mock_cursor = MagicMock()
                mock_cursor.fetchone.return_value = (10, 3, "2024-01-01", "2024-01-15")
                mock_conn.return_value.cursor.return_value = mock_cursor
                stats = get_checkpoint_stats()
                assert "total_checkpoints" in stats
                assert "unique_runs" in stats
                assert "checkpoints_per_run" in stats

    def test_stats_with_no_checkpoints(self):
        """Test stats with empty checkpoint table."""
        with patch('integration_coworker.persistence.checkpoints.get_engine_type') as mock_engine:
            mock_engine.return_value = "sqlite"
            with patch('integration_coworker.persistence.checkpoints.get_connection') as mock_conn:
                mock_cursor = MagicMock()
                mock_cursor.fetchone.return_value = (0, 0, None, None)
                mock_conn.return_value.cursor.return_value = mock_cursor
                stats = get_checkpoint_stats()
                assert stats["total_checkpoints"] == 0
                assert stats["unique_runs"] == 0


class TestPruningDefaultValues:
    """Test pruning default parameter values."""

    def test_default_retention_days(self):
        """Test default retention_days is 7."""
        with patch('integration_coworker.persistence.checkpoints.get_engine_type') as mock_engine:
            mock_engine.return_value = "sqlite"
            with patch('integration_coworker.persistence.checkpoints.get_connection') as mock_conn:
                mock_cursor = MagicMock()
                mock_cursor.fetchall.return_value = []
                mock_conn.return_value.cursor.return_value = mock_cursor
                prune_checkpoints()
                call_args = mock_cursor.execute.call_args_list[0]
                params = call_args[0][1]
                assert 7 in params
