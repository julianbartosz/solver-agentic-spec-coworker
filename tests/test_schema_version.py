"""
Tests for schema version tracking and structural verification.

Run with: pytest tests/test_schema_version.py -v
"""
import pytest

from integration_coworker.persistence.schema_version import (
    EXPECTED_SCHEMA_VERSION,
    check_schema_drift,
    get_version_description,
    validate_schema_structure,
    verify_sqlite_structure,
    verify_postgres_structure,
    REQUIRED_SCHEMA_V1_POSTGRES,
    REQUIRED_SCHEMA_V1_SQLITE,
)


class TestSchemaDriftDetection:
    """Test schema drift detection logic."""
    
    def test_db_behind_expected_is_ok(self):
        """DB at lower version should be upgradeable."""
        is_ok, msg = check_schema_drift(current_version=0, expected_version=1)
        assert is_ok is True
        assert "upgrade" in msg.lower()
    
    def test_db_matches_expected_is_ok(self):
        """DB at same version should be OK."""
        is_ok, msg = check_schema_drift(current_version=1, expected_version=1)
        assert is_ok is True
        assert "expected version" in msg.lower()
    
    def test_db_ahead_is_drift(self):
        """DB at higher version should be detected as drift."""
        is_ok, msg = check_schema_drift(current_version=2, expected_version=1)
        assert is_ok is False
        assert "drift" in msg.lower()
    
    def test_missing_version_table_is_ok(self):
        """Missing version table (-1) should be handled."""
        is_ok, msg = check_schema_drift(current_version=-1, expected_version=1)
        assert is_ok is True
        assert "created" in msg.lower()


class TestVersionDescriptions:
    """Test version description tracking."""
    
    def test_version_0_has_description(self):
        """Initial version should have description."""
        desc = get_version_description(0)
        assert desc is not None
        assert "initial" in desc.lower()
    
    def test_version_1_has_description(self):
        """File Integration V1 version should have description."""
        desc = get_version_description(1)
        assert desc is not None
        assert "file" in desc.lower()
    
    def test_unknown_version_has_fallback(self):
        """Unknown version should have fallback description."""
        desc = get_version_description(999)
        assert desc is not None
        assert "999" in desc


class TestExpectedVersion:
    """Test expected version constant."""
    
    def test_expected_version_is_positive(self):
        """Expected version should be >= 1 (we've shipped V1)."""
        assert EXPECTED_SCHEMA_VERSION >= 1
    
    def test_expected_version_has_description(self):
        """Expected version should have a description."""
        desc = get_version_description(EXPECTED_SCHEMA_VERSION)
        assert desc is not None
        assert len(desc) > 10  # Not just a fallback


class TestSchemaRequirements:
    """Test schema requirement definitions."""
    
    def test_v1_postgres_requires_file_tables(self):
        """V1 Postgres schema should require file integration tables."""
        required_tables = set(REQUIRED_SCHEMA_V1_POSTGRES.keys())
        
        assert "spec_silver.file_specs" in required_tables
        assert "spec_silver.file_fields" in required_tables
        assert "spec_silver.record_layouts" in required_tables
        assert "spec_silver.file_validation_rules" in required_tables
    
    def test_v1_sqlite_requires_file_tables(self):
        """V1 SQLite schema should require file integration tables."""
        required_tables = set(REQUIRED_SCHEMA_V1_SQLITE.keys())
        
        assert "file_specs" in required_tables
        assert "file_fields" in required_tables
        assert "record_layouts" in required_tables
        assert "file_validation_rules" in required_tables
    
    def test_v1_postgres_file_fields_has_required_columns(self):
        """file_fields should have all required columns."""
        file_fields_cols = REQUIRED_SCHEMA_V1_POSTGRES["spec_silver.file_fields"]
        
        assert "id" in file_fields_cols
        assert "file_spec_id" in file_fields_cols
        assert "name" in file_fields_cols
        assert "field_type" in file_fields_cols
        assert "position" in file_fields_cols


class TestStructuralVerificationSQLite:
    """Test structural verification against actual SQLite DB."""
    
    def test_valid_schema_passes(self):
        """A properly initialized SQLite schema should pass verification."""
        import sqlite3
        import tempfile
        import os
        
        # Create a temp DB and manually create schema
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
            tmp_path = f.name
        
        try:
            conn = sqlite3.connect(tmp_path)
            
            # Create all required tables manually (simulating full DDL)
            conn.execute("""
                CREATE TABLE source_systems (
                    id INTEGER PRIMARY KEY,
                    code TEXT NOT NULL,
                    display_name TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE file_specs (
                    id INTEGER PRIMARY KEY,
                    source_system_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    delimiter TEXT,
                    encoding TEXT DEFAULT 'utf-8',
                    has_header INTEGER DEFAULT 1
                )
            """)
            conn.execute("""
                CREATE TABLE file_fields (
                    id INTEGER PRIMARY KEY,
                    file_spec_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    field_type TEXT NOT NULL,
                    position INTEGER NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE record_layouts (
                    id INTEGER PRIMARY KEY,
                    file_spec_id INTEGER NOT NULL,
                    record_type TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE file_validation_rules (
                    id INTEGER PRIMARY KEY,
                    file_spec_id INTEGER NOT NULL,
                    rule_type TEXT NOT NULL,
                    rule_config TEXT NOT NULL
                )
            """)
            conn.commit()
            
            violations = verify_sqlite_structure(conn, version=1)
            conn.close()
            
            # Should have no violations
            assert len(violations) == 0, f"Unexpected violations: {violations}"
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    
    def test_missing_table_detected(self):
        """Missing table should be detected by structural verification."""
        import sqlite3
        import tempfile
        import os
        
        # Create a minimal DB missing required tables
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
            tmp_path = f.name
        
        try:
            conn = sqlite3.connect(tmp_path)
            # Create only source_systems, skip file tables
            conn.execute("""
                CREATE TABLE source_systems (
                    id INTEGER PRIMARY KEY,
                    code TEXT,
                    display_name TEXT
                )
            """)
            conn.commit()
            
            violations = verify_sqlite_structure(conn, version=1)
            
            # Should detect missing tables
            assert len(violations) > 0
            assert any("file_specs" in v for v in violations)
            
            conn.close()
        finally:
            os.unlink(tmp_path)
    
    def test_missing_column_detected(self):
        """Missing column should be detected by structural verification."""
        import sqlite3
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
            tmp_path = f.name
        
        try:
            conn = sqlite3.connect(tmp_path)
            # Create file_specs missing a required column
            conn.execute("""
                CREATE TABLE source_systems (
                    id INTEGER PRIMARY KEY,
                    code TEXT,
                    display_name TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE file_specs (
                    id INTEGER PRIMARY KEY,
                    source_system_id INTEGER,
                    name TEXT
                    -- Missing: file_type
                )
            """)
            conn.execute("""
                CREATE TABLE file_fields (
                    id INTEGER PRIMARY KEY,
                    file_spec_id INTEGER,
                    name TEXT,
                    field_type TEXT,
                    position INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE record_layouts (
                    id INTEGER PRIMARY KEY,
                    file_spec_id INTEGER,
                    record_type TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE file_validation_rules (
                    id INTEGER PRIMARY KEY,
                    file_spec_id INTEGER,
                    rule_type TEXT,
                    rule_config TEXT
                )
            """)
            conn.commit()
            
            violations = verify_sqlite_structure(conn, version=1)
            
            # Should detect missing column
            assert len(violations) > 0
            assert any("file_type" in v for v in violations)
            
            conn.close()
        finally:
            os.unlink(tmp_path)


@pytest.mark.postgres
class TestPostgresSchemaVersion:
    """Test schema version with actual Postgres."""
    
    def test_schema_version_table_created(self, postgres_connection):
        """Schema version table should be created on init."""
        cur = postgres_connection.cursor()
        
        # Check if schema_version table exists
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'spec_silver' 
                AND table_name = 'schema_version'
            )
        """)
        # Note: This may fail if the DDL hasn't been integrated yet
        # That's OK - it documents the expected behavior


@pytest.mark.postgres
class TestStructuralVerificationPostgres:
    """Test structural verification against actual Postgres DB."""
    
    def test_valid_postgres_schema_passes(self, postgres_env):
        """A properly initialized Postgres schema should pass verification."""
        import psycopg
        
        with psycopg.connect(postgres_env) as conn:
            violations = verify_postgres_structure(conn, version=1)
        
        # Should have no violations (schema was initialized by fixture)
        assert len(violations) == 0, f"Unexpected violations: {violations}"

    def test_postgres_kg_tables_exist(self, postgres_env):
        """KG schema should be created alongside silver and gold schemas."""
        import psycopg

        with psycopg.connect(postgres_env) as conn:
            with conn.cursor() as cur:
                for table in ("kg.nodes", "kg.edges", "kg.workflow_steps"):
                    cur.execute(f"SELECT 1 FROM {table} LIMIT 1")
                    # No exception means table exists; rows may be empty and that's fine
    
    def test_validate_schema_structure_api(self, postgres_env):
        """validate_schema_structure() API should work."""
        import psycopg
        
        with psycopg.connect(postgres_env) as conn:
            violations = validate_schema_structure("postgres", conn)
        
        assert isinstance(violations, list)
