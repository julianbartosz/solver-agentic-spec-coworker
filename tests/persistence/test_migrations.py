"""
Tests for the database migration system.

These tests verify the migration runner without requiring a real PostgreSQL database.
"""
import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime

from integration_coworker.persistence.migrations import (
    _compute_checksum,
    _parse_migration_filename,
    list_migration_files,
    MigrationInfo,
    MigrationStatus,
    MIGRATIONS_DIR,
)


class TestMigrationFileParsing:
    """Tests for migration file name parsing."""
    
    def test_valid_migration_filename(self):
        """Valid migration filenames are parsed correctly."""
        result = _parse_migration_filename("001_baseline_v1.sql")
        assert result == ("001", "baseline_v1")
    
    def test_valid_migration_with_underscores(self):
        """Migration names with multiple underscores work."""
        result = _parse_migration_filename("002_add_user_table.sql")
        assert result == ("002", "add_user_table")
    
    def test_invalid_filename_no_number(self):
        """Filenames without version number are rejected."""
        result = _parse_migration_filename("baseline.sql")
        assert result is None
    
    def test_invalid_filename_wrong_extension(self):
        """Filenames with wrong extension are rejected."""
        result = _parse_migration_filename("001_baseline.py")
        assert result is None
    
    def test_invalid_filename_short_number(self):
        """Version numbers must be 3 digits."""
        result = _parse_migration_filename("01_baseline.sql")
        assert result is None
    
    def test_readme_file_ignored(self):
        """README.md is not a migration file."""
        result = _parse_migration_filename("README.md")
        assert result is None


class TestChecksumComputation:
    """Tests for migration checksum computation."""
    
    def test_checksum_deterministic(self):
        """Same content produces same checksum."""
        content = "CREATE TABLE test (id INT);"
        assert _compute_checksum(content) == _compute_checksum(content)
    
    def test_checksum_different_for_different_content(self):
        """Different content produces different checksum."""
        c1 = _compute_checksum("CREATE TABLE a (id INT);")
        c2 = _compute_checksum("CREATE TABLE b (id INT);")
        assert c1 != c2
    
    def test_checksum_is_truncated_sha256(self):
        """Checksum is 16 characters (truncated SHA256)."""
        checksum = _compute_checksum("test")
        assert len(checksum) == 16
        assert all(c in "0123456789abcdef" for c in checksum)


class TestMigrationFileDiscovery:
    """Tests for discovering migration files."""
    
    def test_migrations_directory_exists(self):
        """The migrations directory should exist in the repo."""
        assert MIGRATIONS_DIR.exists(), f"Migrations directory not found: {MIGRATIONS_DIR}"
    
    def test_baseline_migration_exists(self):
        """Baseline migration 001_baseline_v1.sql should exist."""
        baseline = MIGRATIONS_DIR / "001_baseline_v1.sql"
        assert baseline.exists(), "Baseline migration not found"
    
    def test_list_migration_files_returns_sorted(self):
        """Migration files are returned sorted by version."""
        migrations = list_migration_files()
        assert len(migrations) >= 1, "Should have at least baseline migration"
        
        # Check sorted
        versions = [m.version for m in migrations]
        assert versions == sorted(versions)
    
    def test_migration_info_structure(self):
        """MigrationInfo has expected fields."""
        migrations = list_migration_files()
        assert len(migrations) >= 1
        
        m = migrations[0]
        assert isinstance(m, MigrationInfo)
        assert m.version == "001"
        assert m.name == "baseline_v1"
        assert m.path.exists()
        assert len(m.checksum) == 16


class TestMigrationStatusType:
    """Tests for MigrationStatus type."""
    
    def test_migration_status_applied(self):
        """MigrationStatus for applied migration."""
        status = MigrationStatus(
            version="001",
            name="baseline",
            applied=True,
            applied_at=datetime.now(),
            checksum_match=True
        )
        assert status.applied
        assert status.checksum_match
    
    def test_migration_status_pending(self):
        """MigrationStatus for pending migration."""
        status = MigrationStatus(
            version="002",
            name="add_table",
            applied=False,
            applied_at=None,
            checksum_match=True
        )
        assert not status.applied
        assert status.applied_at is None


class TestBaselineMigrationContent:
    """Tests for the baseline migration file content."""
    
    def test_baseline_creates_all_schemas(self):
        """Baseline migration creates all required schemas."""
        baseline = MIGRATIONS_DIR / "001_baseline_v1.sql"
        content = baseline.read_text()
        
        assert "CREATE SCHEMA IF NOT EXISTS spec_silver" in content
        assert "CREATE SCHEMA IF NOT EXISTS spec_bronze" in content
        assert "CREATE SCHEMA IF NOT EXISTS integration_gold" in content
        assert "CREATE SCHEMA IF NOT EXISTS repo_meta" in content
        assert "CREATE SCHEMA IF NOT EXISTS kg" in content
    
    def test_baseline_creates_pgvector_extension(self):
        """Baseline enables pgvector extension."""
        baseline = MIGRATIONS_DIR / "001_baseline_v1.sql"
        content = baseline.read_text()
        
        assert "CREATE EXTENSION IF NOT EXISTS vector" in content
    
    def test_baseline_creates_key_tables(self):
        """Baseline creates essential tables."""
        baseline = MIGRATIONS_DIR / "001_baseline_v1.sql"
        content = baseline.read_text()
        
        # spec_silver tables
        assert "spec_silver.source_systems" in content
        assert "spec_silver.spec_documents" in content
        assert "spec_silver.endpoints" in content
        
        # kg tables
        assert "kg.nodes" in content
        assert "kg.edges" in content
        
        # integration_gold tables
        assert "integration_gold.run_status" in content
    
    def test_baseline_is_idempotent(self):
        """Baseline uses IF NOT EXISTS for all CREATE statements."""
        baseline = MIGRATIONS_DIR / "001_baseline_v1.sql"
        content = baseline.read_text()
        
        # All CREATE TABLE should be IF NOT EXISTS
        import re
        create_tables = re.findall(r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(\S+)", content)
        create_if_not_exists = re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\S+)", content)
        
        # All table creations should use IF NOT EXISTS
        assert len(create_tables) == len(create_if_not_exists), \
            "Some CREATE TABLE statements don't use IF NOT EXISTS"


class TestMigrationRunnerMocked:
    """Tests for migration runner with mocked database connection."""
    
    def test_ensure_migrations_table_creates_table(self):
        """_ensure_migrations_table creates the schema_migrations table."""
        from integration_coworker.persistence.migrations import _ensure_migrations_table
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        
        _ensure_migrations_table(mock_conn)
        
        # Verify CREATE TABLE was executed
        mock_cursor.execute.assert_called_once()
        call_args = mock_cursor.execute.call_args[0][0]
        assert "CREATE TABLE IF NOT EXISTS schema_migrations" in call_args
    
    def test_get_applied_migrations_returns_dict(self):
        """_get_applied_migrations returns dict of version -> (applied_at, checksum)."""
        from integration_coworker.persistence.migrations import _get_applied_migrations
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("001", datetime(2025, 12, 20, 10, 0, 0), "abc123"),
        ]
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        
        result = _get_applied_migrations(mock_conn)
        
        assert "001" in result
        assert result["001"][1] == "abc123"


class TestMigrationModuleImports:
    """Tests that migration module can be imported."""
    
    def test_module_imports(self):
        """Migration module imports without error."""
        from integration_coworker.persistence import migrations
        assert hasattr(migrations, "run_pending_migrations")
        assert hasattr(migrations, "get_migration_status")
        assert hasattr(migrations, "get_current_version")
    
    def test_postgres_imports_migrations(self):
        """Postgres module can import migrations."""
        # This verifies the import path in postgres.py works
        from integration_coworker.persistence.postgres import init_all_schemas
        assert callable(init_all_schemas)
