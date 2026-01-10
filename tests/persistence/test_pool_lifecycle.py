"""
Tests for connection pool lifecycle management.

See docs/PROD_GAP_CLOSURE_PLAN_KG_DB_STREAMING.md Phase 4 for design rationale.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock


# ---------------------------------------------------------------------------
# Tests: Pool Health Check
# ---------------------------------------------------------------------------

class TestConnectionCheck:
    """Tests for _check_connection callback.
    
    V4.1 Fix: check callback must raise on failure (not return bool)
    per psycopg_pool semantics.
    """
    
    @patch("integration_coworker.persistence.postgres.ConnectionPool")
    def test_check_connection_delegates_to_pool_check(self, mock_pool_class):
        """_check_connection should delegate to ConnectionPool.check_connection."""
        from integration_coworker.persistence.postgres import _check_connection
        
        mock_conn = Mock()
        mock_pool_class.check_connection.return_value = None
        
        # Should not raise
        _check_connection(mock_conn)
        
        mock_pool_class.check_connection.assert_called_once_with(mock_conn)
    
    @patch("integration_coworker.persistence.postgres.ConnectionPool")
    def test_check_connection_raises_on_failure(self, mock_pool_class):
        """_check_connection should raise when connection check fails."""
        from integration_coworker.persistence.postgres import _check_connection
        
        mock_conn = Mock()
        mock_pool_class.check_connection.side_effect = Exception("connection closed")
        
        with pytest.raises(Exception, match="connection closed"):
            _check_connection(mock_conn)


# ---------------------------------------------------------------------------
# Tests: Pool Configuration
# ---------------------------------------------------------------------------

class TestPoolConfiguration:
    """Tests for pool configuration parameters."""
    
    @patch("integration_coworker.persistence.postgres.ConnectionPool")
    @patch("integration_coworker.persistence.postgres.get_settings")
    @patch("integration_coworker.persistence.postgres.HAS_PSYCOPG", True)
    def test_pool_has_max_lifetime(self, mock_settings, mock_pool_class):
        """Pool should be configured with max_lifetime for connection recycling."""
        # Reset global pool
        import integration_coworker.persistence.postgres as postgres_module
        postgres_module._pool = None
        
        mock_settings.return_value.database.url = "postgresql://localhost/test"
        mock_pool = Mock()
        mock_pool_class.return_value = mock_pool
        
        postgres_module.get_pool()
        
        # Verify pool was created with max_lifetime
        call_kwargs = mock_pool_class.call_args[1]
        assert "max_lifetime" in call_kwargs
        assert call_kwargs["max_lifetime"] == 3600.0
    
    @patch("integration_coworker.persistence.postgres.ConnectionPool")
    @patch("integration_coworker.persistence.postgres.get_settings")
    @patch("integration_coworker.persistence.postgres.HAS_PSYCOPG", True)
    def test_pool_has_max_idle(self, mock_settings, mock_pool_class):
        """Pool should be configured with max_idle for idle connection recycling."""
        import integration_coworker.persistence.postgres as postgres_module
        postgres_module._pool = None
        
        mock_settings.return_value.database.url = "postgresql://localhost/test"
        mock_pool = Mock()
        mock_pool_class.return_value = mock_pool
        
        postgres_module.get_pool()
        
        call_kwargs = mock_pool_class.call_args[1]
        assert "max_idle" in call_kwargs
        assert call_kwargs["max_idle"] == 300.0
    
    @patch("integration_coworker.persistence.postgres.ConnectionPool")
    @patch("integration_coworker.persistence.postgres.get_settings")
    @patch("integration_coworker.persistence.postgres.HAS_PSYCOPG", True)
    def test_pool_has_check_callback(self, mock_settings, mock_pool_class):
        """Pool should be configured with check callback for validation."""
        import integration_coworker.persistence.postgres as postgres_module
        postgres_module._pool = None
        
        mock_settings.return_value.database.url = "postgresql://localhost/test"
        mock_pool = Mock()
        mock_pool_class.return_value = mock_pool
        
        postgres_module.get_pool()
        
        call_kwargs = mock_pool_class.call_args[1]
        assert "check" in call_kwargs
        # Should be the _check_connection function
        assert callable(call_kwargs["check"])


class TestPoolConfigFromEnv:
    """Tests for pool configuration from environment variables (H-2 hardening)."""
    
    def test_get_pool_config_defaults(self):
        """Test default pool configuration values."""
        from integration_coworker.persistence.postgres import get_pool_config
        
        with patch.dict("os.environ", {}, clear=True):
            config = get_pool_config()
        
        assert config["min_size"] == 2
        assert config["max_size"] == 20
        assert config["timeout"] == 5.0
        assert config["max_lifetime"] == 3600.0
        assert config["max_idle"] == 300.0
    
    def test_get_pool_config_from_env(self):
        """Test pool configuration from environment variables."""
        from integration_coworker.persistence.postgres import get_pool_config
        
        env = {
            "POSTGRES_POOL_MIN": "5",
            "POSTGRES_POOL_MAX": "50",
            "POSTGRES_POOL_TIMEOUT": "10.5",
            "POSTGRES_POOL_MAX_LIFETIME": "7200",
            "POSTGRES_POOL_MAX_IDLE": "600",
        }
        
        with patch.dict("os.environ", env, clear=True):
            config = get_pool_config()
        
        assert config["min_size"] == 5
        assert config["max_size"] == 50
        assert config["timeout"] == 10.5
        assert config["max_lifetime"] == 7200.0
        assert config["max_idle"] == 600.0
    
    def test_get_pool_config_validates_min_values(self):
        """Test that pool config validates minimum values."""
        from integration_coworker.persistence.postgres import get_pool_config
        
        env = {
            "POSTGRES_POOL_MIN": "0",  # Below minimum (1)
            "POSTGRES_POOL_TIMEOUT": "0",  # Below minimum (0.1)
        }
        
        with patch.dict("os.environ", env, clear=True):
            config = get_pool_config()
        
        assert config["min_size"] == 1  # Clamped to minimum
        assert config["timeout"] == 0.1  # Clamped to minimum
    
    def test_get_pool_config_handles_invalid_values(self):
        """Test that invalid env values fall back to defaults."""
        from integration_coworker.persistence.postgres import get_pool_config
        
        env = {
            "POSTGRES_POOL_MIN": "not_a_number",
            "POSTGRES_POOL_TIMEOUT": "invalid",
        }
        
        with patch.dict("os.environ", env, clear=True):
            config = get_pool_config()
        
        # Should use defaults
        assert config["min_size"] == 2
        assert config["timeout"] == 5.0
    
    def test_get_pool_config_ensures_min_le_max(self):
        """Test that min_size is clamped to max_size if min > max."""
        from integration_coworker.persistence.postgres import get_pool_config
        
        env = {
            "POSTGRES_POOL_MIN": "30",
            "POSTGRES_POOL_MAX": "10",
        }
        
        with patch.dict("os.environ", env, clear=True):
            config = get_pool_config()
        
        assert config["min_size"] == 10  # Clamped to max_size
        assert config["max_size"] == 10
    
    @patch("integration_coworker.persistence.postgres.ConnectionPool")
    @patch("integration_coworker.persistence.postgres.get_settings")
    @patch("integration_coworker.persistence.postgres.HAS_PSYCOPG", True)
    def test_pool_uses_env_config(self, mock_settings, mock_pool_class):
        """Test that get_pool() uses environment-configured values."""
        import integration_coworker.persistence.postgres as postgres_module
        postgres_module._pool = None
        postgres_module._pool_config_logged = False
        
        mock_settings.return_value.database.url = "postgresql://localhost/test"
        mock_pool = Mock()
        mock_pool_class.return_value = mock_pool
        
        env = {
            "POSTGRES_POOL_MIN": "3",
            "POSTGRES_POOL_MAX": "15",
        }
        
        with patch.dict("os.environ", env, clear=True):
            postgres_module.get_pool()
        
        call_kwargs = mock_pool_class.call_args[1]
        assert call_kwargs["min_size"] == 3
        assert call_kwargs["max_size"] == 15


# ---------------------------------------------------------------------------
# Tests: Transaction Isolation Helper
# ---------------------------------------------------------------------------

class TestTransactionHelper:
    """Tests for db.transaction() context manager."""
    
    @patch("integration_coworker.persistence.postgres.get_pool")
    @patch("integration_coworker.persistence.db.get_engine_type")
    def test_transaction_uses_psycopg_transaction_api(self, mock_engine, mock_get_pool):
        """transaction() should use psycopg's transaction() context manager for Postgres."""
        from integration_coworker.persistence.db import transaction
        from psycopg import IsolationLevel
        
        mock_engine.return_value = "postgres"
        mock_pool = MagicMock()
        mock_raw_conn = MagicMock()
        mock_pool.getconn.return_value = mock_raw_conn
        mock_get_pool.return_value = mock_pool
        
        # Mock the transaction context manager
        mock_tx_cm = MagicMock()
        mock_raw_conn.transaction.return_value = mock_tx_cm
        mock_tx_cm.__enter__ = MagicMock(return_value=None)
        mock_tx_cm.__exit__ = MagicMock(return_value=False)
        
        with transaction("REPEATABLE READ") as conn:
            pass
        
        # Should have called transaction() with proper isolation level
        mock_raw_conn.transaction.assert_called_once()
        call_kwargs = mock_raw_conn.transaction.call_args[1]
        assert call_kwargs["isolation_level"] == IsolationLevel.REPEATABLE_READ
        
        # Should have returned connection to pool
        mock_pool.putconn.assert_called_once_with(mock_raw_conn)
    
    @patch("integration_coworker.persistence.postgres.get_pool")
    @patch("integration_coworker.persistence.db.get_engine_type")
    def test_transaction_returns_to_pool_on_error(self, mock_engine, mock_get_pool):
        """transaction() should return connection to pool even on exception."""
        from integration_coworker.persistence.db import transaction
        
        mock_engine.return_value = "postgres"
        mock_pool = MagicMock()
        mock_raw_conn = MagicMock()
        mock_pool.getconn.return_value = mock_raw_conn
        mock_get_pool.return_value = mock_pool
        
        # Make the transaction context manager raise
        mock_tx_cm = MagicMock()
        mock_raw_conn.transaction.return_value = mock_tx_cm
        mock_tx_cm.__enter__ = MagicMock(return_value=None)
        mock_tx_cm.__exit__ = MagicMock(side_effect=ValueError("test error"))
        
        with pytest.raises(ValueError):
            with transaction() as conn:
                pass
        
        # Should still return connection to pool
        mock_pool.putconn.assert_called_once_with(mock_raw_conn)
    
    @patch("integration_coworker.persistence.postgres.get_pool")
    @patch("integration_coworker.persistence.db.get_engine_type")
    def test_transaction_default_isolation(self, mock_engine, mock_get_pool):
        """transaction() should default to READ COMMITTED."""
        from integration_coworker.persistence.db import transaction
        from psycopg import IsolationLevel
        
        mock_engine.return_value = "postgres"
        mock_pool = MagicMock()
        mock_raw_conn = MagicMock()
        mock_pool.getconn.return_value = mock_raw_conn
        mock_get_pool.return_value = mock_pool
        
        mock_tx_cm = MagicMock()
        mock_raw_conn.transaction.return_value = mock_tx_cm
        mock_tx_cm.__enter__ = MagicMock(return_value=None)
        mock_tx_cm.__exit__ = MagicMock(return_value=False)
        
        with transaction() as conn:
            pass
        
        call_kwargs = mock_raw_conn.transaction.call_args[1]
        assert call_kwargs["isolation_level"] == IsolationLevel.READ_COMMITTED
    
    @patch("integration_coworker.persistence.db.get_connection")
    @patch("integration_coworker.persistence.db.get_engine_type")
    def test_transaction_skips_isolation_for_sqlite(self, mock_engine, mock_get_conn):
        """transaction() should use simple commit/rollback for SQLite."""
        from integration_coworker.persistence.db import transaction
        
        mock_engine.return_value = "sqlite"
        mock_conn = MagicMock()
        mock_get_conn.return_value = mock_conn
        
        with transaction("SERIALIZABLE") as conn:
            pass
        
        # Should just commit (SQLite doesn't use psycopg's transaction API)
        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: Keepalive Parameters
# ---------------------------------------------------------------------------

class TestKeepaliveParams:
    """Tests for TCP keepalive configuration."""
    
    def test_add_keepalive_params_to_url(self):
        """_add_keepalive_params should add TCP keepalive to URL."""
        from integration_coworker.persistence.postgres import _add_keepalive_params
        
        url = "postgresql://user:pass@localhost:5432/db"
        result = _add_keepalive_params(url)
        
        assert "keepalives=1" in result
        assert "keepalives_idle=60" in result
        assert "keepalives_interval=10" in result
        assert "keepalives_count=5" in result
    
    def test_add_keepalive_params_preserves_existing(self):
        """_add_keepalive_params should not modify if keepalives already present."""
        from integration_coworker.persistence.postgres import _add_keepalive_params
        
        url = "postgresql://user:pass@localhost:5432/db?keepalives=1"
        result = _add_keepalive_params(url)
        
        # Should not have duplicate keepalives
        assert result == url
    
    def test_add_keepalive_params_handles_query_string(self):
        """_add_keepalive_params should use & separator when ? already present."""
        from integration_coworker.persistence.postgres import _add_keepalive_params
        
        url = "postgresql://user:pass@localhost:5432/db?sslmode=require"
        result = _add_keepalive_params(url)
        
        # Should use & separator
        assert "&keepalives=1" in result
        assert result.count("?") == 1
