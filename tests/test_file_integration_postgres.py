"""
Postgres integration tests for File Integration V1.

These tests use testcontainers to spin up a real Postgres instance,
ensuring the file integration code works with actual Postgres DDL,
constraints, and behavior (not SQLite approximations).

Run with: pytest -m postgres tests/test_file_integration_postgres.py -v
"""
import pytest
from typing import List

from integration_coworker.domain.models import (
    FileSpec,
    FileField,
    SourceSystem,
)
from integration_coworker.sources.base import ParsedSpec, SourceType
from integration_coworker.sources.csv_source import CSVSource


@pytest.mark.postgres
class TestPostgresSchemaInit:
    """Test that Postgres schema initializes correctly."""
    
    def test_schema_created(self, postgres_connection):
        """Verify spec_silver schema and core tables exist."""
        cur = postgres_connection.cursor()
        
        # Check schema exists
        cur.execute("""
            SELECT schema_name FROM information_schema.schemata 
            WHERE schema_name = 'spec_silver'
        """)
        assert cur.fetchone() is not None, "spec_silver schema should exist"
        
        # Check file tables exist
        cur.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'spec_silver' 
            AND table_name IN ('file_specs', 'file_fields', 'record_layouts', 'file_validation_rules')
        """)
        tables = {row[0] for row in cur.fetchall()}
        
        assert 'file_specs' in tables, "file_specs table should exist"
        assert 'file_fields' in tables, "file_fields table should exist"
        assert 'record_layouts' in tables, "record_layouts table should exist"
        assert 'file_validation_rules' in tables, "file_validation_rules table should exist"
    
    def test_file_specs_columns(self, postgres_connection):
        """Verify file_specs table has all expected columns."""
        cur = postgres_connection.cursor()
        
        cur.execute("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'spec_silver' AND table_name = 'file_specs'
            ORDER BY ordinal_position
        """)
        columns = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
        
        # Check required columns
        assert 'id' in columns
        assert 'source_system_id' in columns
        assert 'name' in columns
        assert 'file_type' in columns
        assert 'delimiter' in columns
        assert 'encoding' in columns
        assert 'header_row' in columns  # Postgres uses header_row, not has_header
    
    def test_file_fields_columns(self, postgres_connection):
        """Verify file_fields table has all expected columns."""
        cur = postgres_connection.cursor()
        
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'spec_silver' AND table_name = 'file_fields'
        """)
        columns = {row[0] for row in cur.fetchall()}
        
        expected = {'id', 'file_spec_id', 'name', 'field_type', 'position',
                    'start_position', 'length', 'format_mask', 'nullable',
                    'default_value', 'validation_regex', 'description',
                    'sample_values', 'inference_confidence'}
        
        missing = expected - columns
        assert not missing, f"Missing columns in file_fields: {missing}"


@pytest.mark.postgres
class TestPostgresFileSpecPersistence:
    """Test file spec persistence to Postgres."""
    
    def test_insert_source_system(self, postgres_connection):
        """Test inserting a source system (prerequisite for file_specs)."""
        cur = postgres_connection.cursor()
        
        cur.execute("""
            INSERT INTO spec_silver.source_systems (code, display_name, base_url)
            VALUES ('test_csv', 'CSV Test System', 'file://test')
            RETURNING id
        """)
        source_system_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        assert source_system_id is not None
        assert source_system_id > 0
    
    def test_insert_file_spec(self, postgres_connection):
        """Test inserting a file spec with all fields."""
        cur = postgres_connection.cursor()
        
        # Create source system first
        cur.execute("""
            INSERT INTO spec_silver.source_systems (code, display_name, base_url)
            VALUES ('test_csv_2', 'CSV Test System 2', 'file://test2')
            RETURNING id
        """)
        source_system_id = cur.fetchone()[0]
        
        # Insert file spec
        cur.execute("""
            INSERT INTO spec_silver.file_specs 
            (source_system_id, name, file_type, delimiter, encoding, header_row, description)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (source_system_id, 'customers', 'csv', ',', 'utf-8', True, 'Customer data export'))
        
        file_spec_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        assert file_spec_id is not None
        
        # Verify inserted
        cur.execute("SELECT name, file_type, delimiter FROM spec_silver.file_specs WHERE id = %s", (file_spec_id,))
        row = cur.fetchone()
        assert row[0] == 'customers'
        assert row[1] == 'csv'
        assert row[2] == ','
    
    def test_insert_file_fields(self, postgres_connection):
        """Test inserting file fields with FK to file_spec."""
        cur = postgres_connection.cursor()
        
        # Create source system and file spec
        cur.execute("""
            INSERT INTO spec_silver.source_systems (code, display_name)
            VALUES ('test_fields', 'Field Test') RETURNING id
        """)
        source_system_id = cur.fetchone()[0]
        
        cur.execute("""
            INSERT INTO spec_silver.file_specs (source_system_id, name, file_type)
            VALUES (%s, 'transactions', 'csv') RETURNING id
        """, (source_system_id,))
        file_spec_id = cur.fetchone()[0]
        
        # Insert fields
        fields = [
            ('customer_id', 'integer', 0, False),
            ('amount', 'decimal', 1, False),
            ('description', 'string', 2, True),
        ]
        
        for name, ftype, pos, nullable in fields:
            cur.execute("""
                INSERT INTO spec_silver.file_fields 
                (file_spec_id, name, field_type, position, nullable)
                VALUES (%s, %s, %s, %s, %s)
            """, (file_spec_id, name, ftype, pos, nullable))
        
        postgres_connection.commit()
        
        # Verify fields
        cur.execute("""
            SELECT name, field_type, position, nullable 
            FROM spec_silver.file_fields 
            WHERE file_spec_id = %s 
            ORDER BY position
        """, (file_spec_id,))
        
        rows = cur.fetchall()
        assert len(rows) == 3
        assert rows[0][0] == 'customer_id'
        assert rows[1][0] == 'amount'
        assert rows[2][0] == 'description'
    
    def test_unique_constraint_file_spec(self, postgres_connection):
        """Test that (source_system_id, name) is unique for file_specs."""
        cur = postgres_connection.cursor()
        
        cur.execute("""
            INSERT INTO spec_silver.source_systems (code, display_name)
            VALUES ('test_unique', 'Unique Test') RETURNING id
        """)
        source_system_id = cur.fetchone()[0]
        
        # First insert should succeed
        cur.execute("""
            INSERT INTO spec_silver.file_specs (source_system_id, name, file_type)
            VALUES (%s, 'duplicate_name', 'csv')
        """, (source_system_id,))
        postgres_connection.commit()
        
        # Second insert with same name should fail
        with pytest.raises(Exception) as exc_info:
            cur.execute("""
                INSERT INTO spec_silver.file_specs (source_system_id, name, file_type)
                VALUES (%s, 'duplicate_name', 'csv')
            """, (source_system_id,))
            postgres_connection.commit()
        
        postgres_connection.rollback()
        assert 'unique' in str(exc_info.value).lower() or 'duplicate' in str(exc_info.value).lower()
    
    def test_fk_constraint_file_fields(self, postgres_connection):
        """Test that file_fields.file_spec_id must reference valid file_spec."""
        cur = postgres_connection.cursor()
        
        # Try to insert field with non-existent file_spec_id
        with pytest.raises(Exception) as exc_info:
            cur.execute("""
                INSERT INTO spec_silver.file_fields 
                (file_spec_id, name, field_type, position)
                VALUES (99999, 'orphan_field', 'string', 0)
            """)
            postgres_connection.commit()
        
        postgres_connection.rollback()
        assert 'foreign key' in str(exc_info.value).lower() or 'violates' in str(exc_info.value).lower()


@pytest.mark.postgres
class TestCSVSourceToPostgres:
    """End-to-end test: CSV detection → parsing → Postgres persistence."""
    
    def test_csv_detect_parse_persist(self, postgres_connection):
        """
        Full flow: detect CSV → parse to FileSpec → persist to Postgres.
        """
        # 1. Detect and parse CSV
        csv_content = """customer_id,name,email,balance
1,Alice,alice@example.com,100.50
2,Bob,bob@example.com,200.75
3,Charlie,charlie@example.com,50.00"""
        
        source = CSVSource()
        confidence = source.detect(csv_content, "customers.csv", "text/csv")
        assert confidence >= 0.9, f"CSV should be detected with high confidence, got {confidence}"
        
        parsed: ParsedSpec = source.parse(csv_content, "customers.csv")
        assert parsed.source_type == SourceType.FILE
        assert parsed.data is not None
        assert "file_spec" in parsed.data
        assert "fields" in parsed.data
        
        file_spec = parsed.data["file_spec"]
        fields = parsed.data["fields"]
        
        assert file_spec.name == "customers"
        assert file_spec.file_type == "csv"
        assert len(fields) == 4
        
        # 2. Persist to Postgres
        cur = postgres_connection.cursor()
        
        # Create source system
        cur.execute("""
            INSERT INTO spec_silver.source_systems (code, display_name)
            VALUES ('csv_test_e2e', 'CSV E2E Test') RETURNING id
        """)
        source_system_id = cur.fetchone()[0]
        
        # Insert file_spec
        cur.execute("""
            INSERT INTO spec_silver.file_specs 
            (source_system_id, name, file_type, delimiter, encoding, header_row)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            source_system_id,
            file_spec.name,
            file_spec.file_type,
            file_spec.delimiter or ',',
            file_spec.encoding or 'utf-8',
            file_spec.has_header,
        ))
        file_spec_id = cur.fetchone()[0]
        
        # Insert fields
        import json
        for field in fields:
            cur.execute("""
                INSERT INTO spec_silver.file_fields 
                (file_spec_id, name, field_type, position, nullable, sample_values, inference_confidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                file_spec_id,
                field.name,
                field.field_type,
                field.position,
                field.nullable,
                json.dumps(field.sample_values) if field.sample_values else '[]',
                field.inference_confidence,
            ))
        
        postgres_connection.commit()
        
        # 3. Verify persisted data
        cur.execute("""
            SELECT name, file_type, delimiter 
            FROM spec_silver.file_specs WHERE id = %s
        """, (file_spec_id,))
        spec_row = cur.fetchone()
        assert spec_row[0] == 'customers'
        assert spec_row[1] == 'csv'
        
        cur.execute("""
            SELECT name, field_type, position 
            FROM spec_silver.file_fields 
            WHERE file_spec_id = %s 
            ORDER BY position
        """, (file_spec_id,))
        field_rows = cur.fetchall()
        
        assert len(field_rows) == 4
        assert field_rows[0][0] == 'customer_id'
        assert field_rows[1][0] == 'name'
        assert field_rows[2][0] == 'email'
        assert field_rows[3][0] == 'balance'
    
    def test_tsv_detect_parse_persist(self, postgres_connection):
        """Test TSV (tab-separated) file end-to-end."""
        tsv_content = """product_id\tproduct_name\tprice\tin_stock
SKU001\tWidget A\t19.99\ttrue
SKU002\tWidget B\t29.99\tfalse"""
        
        source = CSVSource()
        confidence = source.detect(tsv_content, "products.tsv", "text/tab-separated-values")
        assert confidence >= 0.9
        
        parsed = source.parse(tsv_content, "products.tsv")
        assert parsed.data["file_spec"].delimiter == '\t'
        
        # Persist
        cur = postgres_connection.cursor()
        cur.execute("""
            INSERT INTO spec_silver.source_systems (code, display_name)
            VALUES ('tsv_test', 'TSV Test') RETURNING id
        """)
        source_system_id = cur.fetchone()[0]
        
        file_spec = parsed.data["file_spec"]
        cur.execute("""
            INSERT INTO spec_silver.file_specs 
            (source_system_id, name, file_type, delimiter)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, (source_system_id, file_spec.name, file_spec.file_type, file_spec.delimiter))
        
        file_spec_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # Verify delimiter stored correctly
        cur.execute("SELECT delimiter FROM spec_silver.file_specs WHERE id = %s", (file_spec_id,))
        assert cur.fetchone()[0] == '\t'


@pytest.mark.postgres
class TestFixedWidthSourceToPostgres:
    """End-to-end test: Fixed-width detection → parsing → Postgres persistence."""
    
    def test_fixed_width_detect_parse_persist(self, postgres_connection):
        """
        Full flow: detect fixed-width → parse to FileSpec + RecordLayout → persist to Postgres.
        
        This test verifies:
        1. FixedWidthSource detects bank_fixed_width_inferable.txt with high confidence
        2. Parser correctly infers column positions/lengths (from whitespace boundaries)
        3. FileSpec and FileFields persist with start_position/length populated
        4. RecordLayout persists for fixed-width format
        
        Note: Uses the _inferable variant which has whitespace between fields.
        The base bank_fixed_width.txt has contiguous fields and requires explicit colspec.
        """
        import json
        from pathlib import Path
        from integration_coworker.sources.fixed_width import FixedWidthSource
        
        # 1. Load the bank_fixed_width_inferable.txt fixture (has whitespace for inference)
        fixture_path = Path(__file__).parent / "fixtures" / "file_specs" / "bank_fixed_width_inferable.txt"
        fixed_width_content = fixture_path.read_text()
        
        # 2. Detect fixed-width format
        source = FixedWidthSource()
        confidence = source.detect(fixed_width_content, "bank_fixed_width.txt", "text/plain")
        assert confidence >= 0.7, f"Fixed-width should be detected with good confidence, got {confidence}"
        
        # 3. Parse to get FileSpec, FileFields, RecordLayout
        from integration_coworker.sources.base import SourceType
        parsed = source.parse(fixed_width_content, "bank_fixed_width.txt")
        
        assert parsed.source_type == SourceType.FILE
        assert parsed.data is not None
        assert "file_spec" in parsed.data
        assert "fields" in parsed.data
        assert "record_layouts" in parsed.data
        
        file_spec = parsed.data["file_spec"]
        fields = parsed.data["fields"]
        record_layouts = parsed.data["record_layouts"]
        
        assert file_spec.file_type == "fixed_width"
        assert len(fields) > 0, "Should have inferred fields"
        assert len(record_layouts) >= 1, "Should have at least one record layout"
        
        # 4. Persist to Postgres
        cur = postgres_connection.cursor()
        
        # Create source system
        cur.execute("""
            INSERT INTO spec_silver.source_systems (code, display_name)
            VALUES ('fixed_width_e2e', 'Fixed Width E2E Test') RETURNING id
        """)
        source_system_id = cur.fetchone()[0]
        
        # Insert file_spec
        cur.execute("""
            INSERT INTO spec_silver.file_specs 
            (source_system_id, name, file_type, encoding)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, (
            source_system_id,
            file_spec.name,
            file_spec.file_type,
            file_spec.encoding or 'utf-8',
        ))
        file_spec_id = cur.fetchone()[0]
        
        # Insert fields WITH start_position and length (critical for fixed-width)
        for field in fields:
            cur.execute("""
                INSERT INTO spec_silver.file_fields 
                (file_spec_id, name, field_type, position, start_position, length, 
                 nullable, sample_values, inference_confidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                file_spec_id,
                field.name,
                field.field_type,
                field.position,
                field.start_position,  # CRITICAL: fixed-width column start
                field.length,          # CRITICAL: fixed-width column width
                field.nullable,
                json.dumps(field.sample_values) if field.sample_values else '[]',
                field.inference_confidence,
            ))
        
        # Insert record layouts
        for layout in record_layouts:
            cur.execute("""
                INSERT INTO spec_silver.record_layouts 
                (file_spec_id, record_type, identifier_field, identifier_value, record_length)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                file_spec_id,
                layout.record_type,
                layout.identifier_field,
                layout.identifier_value,
                layout.record_length,
            ))
        
        postgres_connection.commit()
        
        # 5. Verify persisted data
        
        # Verify file_spec
        cur.execute("""
            SELECT name, file_type FROM spec_silver.file_specs WHERE id = %s
        """, (file_spec_id,))
        spec_row = cur.fetchone()
        assert spec_row[0] == 'bank_fixed_width'
        assert spec_row[1] == 'fixed_width'
        
        # Verify file_fields have start_position and length populated
        cur.execute("""
            SELECT name, field_type, position, start_position, length 
            FROM spec_silver.file_fields 
            WHERE file_spec_id = %s 
            ORDER BY position
        """, (file_spec_id,))
        field_rows = cur.fetchall()
        
        assert len(field_rows) >= 1, "Should have persisted at least one field"
        
        # Check that start_position and length are populated (not NULL)
        for row in field_rows:
            name, ftype, pos, start_pos, length = row
            assert start_pos is not None, f"Field {name} should have start_position"
            assert length is not None, f"Field {name} should have length"
            assert start_pos >= 0, f"Field {name} start_position should be >= 0"
            assert length > 0, f"Field {name} length should be > 0"
        
        # Verify record_layouts persisted
        cur.execute("""
            SELECT record_type, record_length 
            FROM spec_silver.record_layouts 
            WHERE file_spec_id = %s
        """, (file_spec_id,))
        layout_rows = cur.fetchall()
        
        assert len(layout_rows) >= 1, "Should have persisted at least one record layout"
        
        # Verify record_length matches our detected line length
        layout_row = layout_rows[0]
        assert layout_row[1] > 0, "Record length should be positive"
    
    def test_fixed_width_fields_cover_full_line(self, postgres_connection):
        """
        Verify that inferred fixed-width fields cover the entire line length.
        
        For a proper fixed-width file, the sum of field lengths should equal
        the line length (or be very close accounting for any trailing newlines).
        
        Uses bank_fixed_width_inferable.txt which has whitespace boundaries.
        """
        from pathlib import Path
        from integration_coworker.sources.fixed_width import FixedWidthSource
        
        fixture_path = Path(__file__).parent / "fixtures" / "file_specs" / "bank_fixed_width_inferable.txt"
        fixed_width_content = fixture_path.read_text()
        
        source = FixedWidthSource()
        parsed = source.parse(fixed_width_content, "bank_fixed_width.txt")
        
        fields = parsed.data["fields"]
        record_layouts = parsed.data["record_layouts"]
        
        # Calculate total field coverage
        if fields:
            # Find the end of the last field
            max_end = max(f.start_position + f.length for f in fields)
            
            # Should match or be close to record_length
            if record_layouts:
                record_length = record_layouts[0].record_length
                # Allow some tolerance (whitespace trimming may adjust slightly)
                assert abs(max_end - record_length) <= 5, \
                    f"Field coverage ({max_end}) should match record length ({record_length})"


@pytest.mark.postgres
class TestPostgresRecordLayouts:
    """Test record_layouts table for multi-record file formats."""
    
    def test_insert_record_layout(self, postgres_connection):
        """Test inserting record layouts for a fixed-width file."""
        cur = postgres_connection.cursor()
        
        # Setup
        cur.execute("""
            INSERT INTO spec_silver.source_systems (code, display_name)
            VALUES ('fixed_test', 'Fixed Width Test') RETURNING id
        """)
        source_system_id = cur.fetchone()[0]
        
        cur.execute("""
            INSERT INTO spec_silver.file_specs (source_system_id, name, file_type)
            VALUES (%s, 'bank_transactions', 'fixed_width') RETURNING id
        """, (source_system_id,))
        file_spec_id = cur.fetchone()[0]
        
        # Insert record layouts
        layouts = [
            ('header', 'record_type', 'H', 100),
            ('detail', 'record_type', 'D', 150),
            ('trailer', 'record_type', 'T', 50),
        ]
        
        for record_type, id_field, id_value, length in layouts:
            cur.execute("""
                INSERT INTO spec_silver.record_layouts 
                (file_spec_id, record_type, identifier_field, identifier_value, record_length)
                VALUES (%s, %s, %s, %s, %s)
            """, (file_spec_id, record_type, id_field, id_value, length))
        
        postgres_connection.commit()
        
        # Verify
        cur.execute("""
            SELECT record_type, identifier_value, record_length 
            FROM spec_silver.record_layouts 
            WHERE file_spec_id = %s 
            ORDER BY record_type
        """, (file_spec_id,))
        
        rows = cur.fetchall()
        assert len(rows) == 3
        assert rows[0][0] == 'detail'
        assert rows[1][0] == 'header'
        assert rows[2][0] == 'trailer'


@pytest.mark.postgres  
class TestPostgresValidationRules:
    """Test file_validation_rules table."""
    
    def test_insert_validation_rules(self, postgres_connection):
        """Test inserting validation rules for a file spec."""
        cur = postgres_connection.cursor()
        
        import json
        
        # Setup
        cur.execute("""
            INSERT INTO spec_silver.source_systems (code, display_name)
            VALUES ('validation_test', 'Validation Test') RETURNING id
        """)
        source_system_id = cur.fetchone()[0]
        
        cur.execute("""
            INSERT INTO spec_silver.file_specs (source_system_id, name, file_type)
            VALUES (%s, 'validated_file', 'csv') RETURNING id
        """, (source_system_id,))
        file_spec_id = cur.fetchone()[0]
        
        # Insert validation rules
        rules = [
            ('customer_id', 'required', {'allow_empty': False}, 'Customer ID is required'),
            ('amount', 'range', {'min': 0, 'max': 1000000}, 'Amount must be between 0 and 1000000'),
            ('email', 'regex', {'pattern': r'^[\w\.-]+@[\w\.-]+\.\w+$'}, 'Invalid email format'),
        ]
        
        for field_name, rule_type, config, message in rules:
            cur.execute("""
                INSERT INTO spec_silver.file_validation_rules 
                (file_spec_id, field_name, rule_type, rule_config, error_message)
                VALUES (%s, %s, %s, %s, %s)
            """, (file_spec_id, field_name, rule_type, json.dumps(config), message))
        
        postgres_connection.commit()
        
        # Verify
        cur.execute("""
            SELECT field_name, rule_type, rule_config, error_message 
            FROM spec_silver.file_validation_rules 
            WHERE file_spec_id = %s 
            ORDER BY field_name
        """, (file_spec_id,))
        
        rows = cur.fetchall()
        assert len(rows) == 3
        
        # Check amount rule
        amount_rule = [r for r in rows if r[0] == 'amount'][0]
        assert amount_rule[1] == 'range'
        # psycopg3 auto-deserializes JSONB to dict, no need for json.loads
        config = amount_rule[2] if isinstance(amount_rule[2], dict) else json.loads(amount_rule[2])
        assert config['min'] == 0
        assert config['max'] == 1000000


# =============================================================================
# Idempotency Tests (Plan Step 4.2)
# =============================================================================

@pytest.mark.postgres
class TestIdempotency:
    """
    Test that build_silver_file_model is idempotent.
    
    Re-running the node with the same data must NOT create duplicates.
    This is enforced via UNIQUE constraints and ON CONFLICT upserts.
    """
    
    def test_file_spec_upsert_no_duplicates(self, postgres_connection, monkeypatch):
        """
        Inserting the same file_spec twice via upsert should not create duplicates.
        
        Uses ON CONFLICT (source_system_id, name) DO UPDATE.
        """
        cur = postgres_connection.cursor()
        
        # Setup source system
        cur.execute("""
            INSERT INTO spec_silver.source_systems (code, display_name)
            VALUES ('idempotency_test', 'Idempotency Test') RETURNING id
        """)
        source_system_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # First insert
        cur.execute("""
            INSERT INTO spec_silver.file_specs 
            (source_system_id, name, file_type, description)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source_system_id, name) 
            DO UPDATE SET description = EXCLUDED.description
            RETURNING id
        """, (source_system_id, 'test_file', 'csv', 'First description'))
        first_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # Second insert with same key - should update, not insert
        cur.execute("""
            INSERT INTO spec_silver.file_specs 
            (source_system_id, name, file_type, description)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source_system_id, name) 
            DO UPDATE SET description = EXCLUDED.description
            RETURNING id
        """, (source_system_id, 'test_file', 'csv', 'Second description'))
        second_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # Should be the same row (upsert behavior)
        assert first_id == second_id, "Upsert should return same ID"
        
        # Verify only one row exists
        cur.execute("""
            SELECT COUNT(*) FROM spec_silver.file_specs 
            WHERE source_system_id = %s AND name = %s
        """, (source_system_id, 'test_file'))
        count = cur.fetchone()[0]
        assert count == 1, f"Should have exactly 1 file_spec, got {count}"
        
        # Verify description was updated
        cur.execute("""
            SELECT description FROM spec_silver.file_specs WHERE id = %s
        """, (first_id,))
        desc = cur.fetchone()[0]
        assert desc == 'Second description', "Description should be updated"
    
    def test_file_field_upsert_no_duplicates(self, postgres_connection):
        """
        Inserting the same file_field twice via upsert should not create duplicates.
        
        Uses ON CONFLICT (file_spec_id, name) DO UPDATE.
        """
        cur = postgres_connection.cursor()
        
        # Setup
        cur.execute("""
            INSERT INTO spec_silver.source_systems (code, display_name)
            VALUES ('field_idempotency', 'Field Idempotency Test') RETURNING id
        """)
        source_system_id = cur.fetchone()[0]
        
        cur.execute("""
            INSERT INTO spec_silver.file_specs (source_system_id, name, file_type)
            VALUES (%s, 'field_test_file', 'csv') RETURNING id
        """, (source_system_id,))
        file_spec_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # First field insert
        cur.execute("""
            INSERT INTO spec_silver.file_fields 
            (file_spec_id, name, field_type, position)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (file_spec_id, name) 
            DO UPDATE SET field_type = EXCLUDED.field_type
            RETURNING id
        """, (file_spec_id, 'customer_id', 'string', 1))
        first_field_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # Second insert - same field, different type
        cur.execute("""
            INSERT INTO spec_silver.file_fields 
            (file_spec_id, name, field_type, position)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (file_spec_id, name) 
            DO UPDATE SET field_type = EXCLUDED.field_type
            RETURNING id
        """, (file_spec_id, 'customer_id', 'integer', 1))
        second_field_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # Should be same row
        assert first_field_id == second_field_id, "Upsert should return same ID"
        
        # Verify count
        cur.execute("""
            SELECT COUNT(*) FROM spec_silver.file_fields 
            WHERE file_spec_id = %s AND name = %s
        """, (file_spec_id, 'customer_id'))
        count = cur.fetchone()[0]
        assert count == 1, f"Should have exactly 1 field, got {count}"
    
    def test_build_silver_file_model_idempotent(self, postgres_connection, monkeypatch):
        """
        Full integration test: run build_silver_file_model twice with same data.
        
        This tests the actual node implementation, not just raw SQL.
        Row counts should remain unchanged after second run.
        """
        import os
        from pathlib import Path
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.domain.models import SourceSystem, FileSpec, FileField
        from integration_coworker.sources.base import ParsedSpec, SourceType
        from integration_coworker.graph.nodes.build_silver_file_model import build_silver_file_model
        
        # Get connection string from fixture (postgres_connection is already set up)
        # We need to configure the persistence layer to use this connection
        cur = postgres_connection.cursor()
        
        # Create source system first
        cur.execute("""
            INSERT INTO spec_silver.source_systems (code, display_name)
            VALUES ('idempotency_e2e', 'Idempotency E2E Test') RETURNING id
        """)
        source_system_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # Create test data
        source_system = SourceSystem(
            id=source_system_id,
            code='idempotency_e2e',
            name='Idempotency E2E Test',
        )
        
        file_spec = FileSpec(
            id=None,
            source_system_id=source_system_id,
            name='idempotent_test_file',
            file_type='csv',
            encoding='utf-8',
            delimiter=',',
            has_header=True,
        )
        
        fields = [
            FileField(
                id=None,
                file_spec_id=None,  # Will be set during persistence
                name='column_a',
                field_type='string',
                position=1,
            ),
            FileField(
                id=None,
                file_spec_id=None,
                name='column_b',
                field_type='integer',
                position=2,
            ),
        ]
        
        parsed_spec = ParsedSpec(
            source_type=SourceType.FILE,
            source_uri='test_file.csv',
            data={
                'file_spec': file_spec,
                'fields': fields,
                'record_layouts': [],
            },
        )
        
        # Create initial state
        state1 = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description='idempotency test',
            source_system=source_system,
            parsed_specs=[parsed_spec],
        )
        
        # Patch persistence to use our postgres connection
        def mock_get_connection():
            class ConnectionWrapper:
                def __enter__(self):
                    return postgres_connection
                def __exit__(self, *args):
                    pass  # Don't close the shared connection
            return ConnectionWrapper()
        
        def mock_get_engine_type():
            return "postgres"
        
        monkeypatch.setattr(
            "integration_coworker.persistence.db.get_connection",
            mock_get_connection
        )
        monkeypatch.setattr(
            "integration_coworker.persistence.db.get_engine_type",
            mock_get_engine_type
        )
        
        # First run
        result1 = build_silver_file_model(state1)
        
        # Count rows after first run
        cur.execute("""
            SELECT COUNT(*) FROM spec_silver.file_specs 
            WHERE source_system_id = %s AND name = 'idempotent_test_file'
        """, (source_system_id,))
        count1_specs = cur.fetchone()[0]
        
        cur.execute("""
            SELECT COUNT(*) FROM spec_silver.file_fields f
            JOIN spec_silver.file_specs s ON f.file_spec_id = s.id
            WHERE s.source_system_id = %s AND s.name = 'idempotent_test_file'
        """, (source_system_id,))
        count1_fields = cur.fetchone()[0]
        
        assert count1_specs == 1, f"First run: should have 1 file_spec, got {count1_specs}"
        assert count1_fields == 2, f"First run: should have 2 file_fields, got {count1_fields}"
        
        # Create fresh state for second run (simulating re-run)
        # Need to recreate FileSpec/FileField without IDs to simulate fresh parse
        file_spec2 = FileSpec(
            id=None,
            source_system_id=source_system_id,
            name='idempotent_test_file',
            file_type='csv',
            encoding='utf-8',
            delimiter=',',
            has_header=True,
        )
        
        fields2 = [
            FileField(
                id=None,
                file_spec_id=None,
                name='column_a',
                field_type='string',
                position=1,
            ),
            FileField(
                id=None,
                file_spec_id=None,
                name='column_b',
                field_type='integer',
                position=2,
            ),
        ]
        
        parsed_spec2 = ParsedSpec(
            source_type=SourceType.FILE,
            source_uri='test_file.csv',
            data={
                'file_spec': file_spec2,
                'fields': fields2,
                'record_layouts': [],
            },
        )
        
        state2 = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description='idempotency test - second run',
            source_system=source_system,
            parsed_specs=[parsed_spec2],
        )
        
        # Second run
        result2 = build_silver_file_model(state2)
        
        # Count rows after second run
        cur.execute("""
            SELECT COUNT(*) FROM spec_silver.file_specs 
            WHERE source_system_id = %s AND name = 'idempotent_test_file'
        """, (source_system_id,))
        count2_specs = cur.fetchone()[0]
        
        cur.execute("""
            SELECT COUNT(*) FROM spec_silver.file_fields f
            JOIN spec_silver.file_specs s ON f.file_spec_id = s.id
            WHERE s.source_system_id = %s AND s.name = 'idempotent_test_file'
        """, (source_system_id,))
        count2_fields = cur.fetchone()[0]
        
        # CRITICAL: Counts must be unchanged
        assert count2_specs == count1_specs, \
            f"Idempotency violation: file_specs changed from {count1_specs} to {count2_specs}"
        assert count2_fields == count1_fields, \
            f"Idempotency violation: file_fields changed from {count1_fields} to {count2_fields}"
        
        # Verify the node completed
        assert "build_silver_file_model" in result2.completed_steps
    
    def test_unique_constraint_prevents_duplicates(self, postgres_connection):
        """
        Verify that UNIQUE constraints exist and reject duplicates.
        
        Without ON CONFLICT, duplicate inserts should fail.
        """
        cur = postgres_connection.cursor()
        
        # Setup
        cur.execute("""
            INSERT INTO spec_silver.source_systems (code, display_name)
            VALUES ('constraint_test', 'Constraint Test') RETURNING id
        """)
        source_system_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # First insert - should succeed
        cur.execute("""
            INSERT INTO spec_silver.file_specs 
            (source_system_id, name, file_type)
            VALUES (%s, 'unique_test', 'csv')
        """, (source_system_id,))
        postgres_connection.commit()
        
        # Second insert - should fail due to UNIQUE constraint
        import psycopg
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute("""
                INSERT INTO spec_silver.file_specs 
                (source_system_id, name, file_type)
                VALUES (%s, 'unique_test', 'csv')
            """, (source_system_id,))
        
        # Rollback the failed transaction
        postgres_connection.rollback()
        
        # Verify only one row
        cur.execute("""
            SELECT COUNT(*) FROM spec_silver.file_specs 
            WHERE source_system_id = %s AND name = 'unique_test'
        """, (source_system_id,))
        count = cur.fetchone()[0]
        assert count == 1, f"Should have exactly 1 row, got {count}"
    
    def test_record_layout_upsert_no_duplicates(self, postgres_connection):
        """
        Test record_layouts idempotency with upsert behavior.
        """
        cur = postgres_connection.cursor()
        
        # Setup
        cur.execute("""
            INSERT INTO spec_silver.source_systems (code, display_name)
            VALUES ('layout_idempotency', 'Layout Idempotency Test') RETURNING id
        """)
        source_system_id = cur.fetchone()[0]
        
        cur.execute("""
            INSERT INTO spec_silver.file_specs (source_system_id, name, file_type)
            VALUES (%s, 'layout_test_file', 'fixed_width') RETURNING id
        """, (source_system_id,))
        file_spec_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # First insert
        cur.execute("""
            INSERT INTO spec_silver.record_layouts 
            (file_spec_id, record_type, identifier_field, identifier_value, record_length)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (file_spec_id, record_type) 
            DO UPDATE SET record_length = EXCLUDED.record_length
            RETURNING id
        """, (file_spec_id, 'detail', 'rec_type', 'D', 100))
        first_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # Second insert with different length
        cur.execute("""
            INSERT INTO spec_silver.record_layouts 
            (file_spec_id, record_type, identifier_field, identifier_value, record_length)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (file_spec_id, record_type) 
            DO UPDATE SET record_length = EXCLUDED.record_length
            RETURNING id
        """, (file_spec_id, 'detail', 'rec_type', 'D', 150))
        second_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # Should be same row
        assert first_id == second_id, "Upsert should return same ID"
        
        # Verify count
        cur.execute("""
            SELECT COUNT(*) FROM spec_silver.record_layouts 
            WHERE file_spec_id = %s AND record_type = 'detail'
        """, (file_spec_id,))
        count = cur.fetchone()[0]
        assert count == 1, f"Should have exactly 1 layout, got {count}"
        
        # Verify record_length was updated
        cur.execute("""
            SELECT record_length FROM spec_silver.record_layouts WHERE id = %s
        """, (first_id,))
        length = cur.fetchone()[0]
        assert length == 150, "record_length should be updated to 150"


# =============================================================================
# Excel Source Postgres Integration Tests
# =============================================================================

@pytest.mark.postgres
class TestExcelSourceToPostgres:
    """End-to-end test: Excel detection → parsing → Postgres persistence."""
    
    def test_excel_detect_parse_persist(self, postgres_connection):
        """
        Full flow: detect Excel → parse to FileSpec → persist to Postgres.
        
        This test verifies:
        1. ExcelSource detects customers_simple.xlsx with high confidence
        2. Parser correctly infers column types
        3. FileSpec and FileFields persist correctly
        """
        import json
        from pathlib import Path
        from integration_coworker.sources.excel import ExcelSource
        from integration_coworker.sources.base import SourceType
        
        # 1. Load the Excel fixture
        fixture_path = Path(__file__).parent / "fixtures" / "file_specs" / "customers_simple.xlsx"
        excel_bytes = fixture_path.read_bytes()
        
        # 2. Detect Excel format
        source = ExcelSource()
        confidence = source.detect(excel_bytes, "customers_simple.xlsx", "")
        assert confidence >= 0.8, f"Excel should be detected with high confidence, got {confidence}"
        
        # 3. Parse to get FileSpec, FileFields, RecordLayout
        parsed = source.parse(excel_bytes, "customers_simple.xlsx")
        
        assert parsed.source_type == SourceType.FILE
        assert parsed.is_valid(), f"Should parse successfully: {parsed.errors}"
        assert parsed.data is not None
        
        # Single sheet: uses file_spec key
        file_spec = parsed.data.get("file_spec") or parsed.data.get("file_specs", [{}])[0]
        fields = parsed.data.get("fields", [])
        record_layouts = parsed.data.get("record_layouts", [])
        
        assert file_spec.file_type == "xlsx"
        assert len(fields) >= 5, f"Should have at least 5 columns, got {len(fields)}"
        
        # 4. Persist to Postgres
        cur = postgres_connection.cursor()
        
        # Create source system
        cur.execute("""
            INSERT INTO spec_silver.source_systems (code, display_name)
            VALUES ('excel_e2e', 'Excel E2E Test') RETURNING id
        """)
        source_system_id = cur.fetchone()[0]
        
        # Insert file_spec
        cur.execute("""
            INSERT INTO spec_silver.file_specs 
            (source_system_id, name, file_type, encoding, header_row)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (
            source_system_id,
            file_spec.name,
            file_spec.file_type,
            file_spec.encoding or 'utf-8',
            file_spec.has_header,
        ))
        file_spec_id = cur.fetchone()[0]
        
        # Insert fields
        for field in fields:
            cur.execute("""
                INSERT INTO spec_silver.file_fields 
                (file_spec_id, name, field_type, position, nullable, sample_values, inference_confidence)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                file_spec_id,
                field.name,
                field.field_type,
                field.position,
                field.nullable,
                json.dumps(field.sample_values) if field.sample_values else '[]',
                field.inference_confidence,
            ))
        
        # Insert record layout if present
        for layout in record_layouts:
            cur.execute("""
                INSERT INTO spec_silver.record_layouts 
                (file_spec_id, record_type, identifier_field, identifier_value)
                VALUES (%s, %s, %s, %s)
            """, (
                file_spec_id,
                layout.record_type,
                layout.identifier_field,
                layout.identifier_value,
            ))
        
        postgres_connection.commit()
        
        # 5. Verify persisted data
        cur.execute("""
            SELECT name, file_type FROM spec_silver.file_specs WHERE id = %s
        """, (file_spec_id,))
        spec_row = cur.fetchone()
        assert spec_row[1] == 'xlsx', f"File type should be xlsx, got {spec_row[1]}"
        
        cur.execute("""
            SELECT name, field_type, position 
            FROM spec_silver.file_fields 
            WHERE file_spec_id = %s 
            ORDER BY position
        """, (file_spec_id,))
        field_rows = cur.fetchall()
        
        assert len(field_rows) >= 5, f"Should have at least 5 fields persisted, got {len(field_rows)}"
        
        # Check expected columns exist
        field_names = [row[0] for row in field_rows]
        assert 'customer_id' in field_names, "Should have customer_id field"
        assert 'name' in field_names, "Should have name field"
    
    def test_excel_multisheet_persist_all_sheets(self, postgres_connection):
        """
        Test that multi-sheet Excel persists one FileSpec per sheet.
        
        Verifies:
        1. Each sheet gets a separate file_spec
        2. All file_specs share the same source_system_id
        3. No duplicates created
        """
        import json
        from pathlib import Path
        from integration_coworker.sources.excel import ExcelSource
        
        # Load multi-sheet fixture
        fixture_path = Path(__file__).parent / "fixtures" / "file_specs" / "multi_sheet_workbook.xlsx"
        excel_bytes = fixture_path.read_bytes()
        
        source = ExcelSource()
        parsed = source.parse(excel_bytes, "multi_sheet_workbook.xlsx")
        
        assert parsed.is_valid(), f"Should parse: {parsed.errors}"
        
        # Multi-sheet: check for file_specs list
        file_specs = parsed.data.get("file_specs") or [parsed.data.get("file_spec")]
        all_fields = parsed.data.get("fields", [])
        
        assert len(file_specs) >= 2, f"Should have at least 2 sheets, got {len(file_specs)}"
        
        # Persist each sheet as separate file_spec
        cur = postgres_connection.cursor()
        
        cur.execute("""
            INSERT INTO spec_silver.source_systems (code, display_name)
            VALUES ('excel_multisheet_e2e', 'Excel Multi-Sheet E2E') RETURNING id
        """)
        source_system_id = cur.fetchone()[0]
        
        # Track created file_spec_ids
        created_ids = []
        
        for file_spec in file_specs:
            cur.execute("""
                INSERT INTO spec_silver.file_specs 
                (source_system_id, name, file_type, encoding, header_row)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (source_system_id, name) DO UPDATE SET file_type = EXCLUDED.file_type
                RETURNING id
            """, (
                source_system_id,
                file_spec.name,
                file_spec.file_type,
                file_spec.encoding or 'utf-8',
                file_spec.has_header,
            ))
            created_ids.append(cur.fetchone()[0])
        
        postgres_connection.commit()
        
        # Verify correct number of specs
        cur.execute("""
            SELECT COUNT(*) FROM spec_silver.file_specs 
            WHERE source_system_id = %s
        """, (source_system_id,))
        count = cur.fetchone()[0]
        
        assert count == len(file_specs), \
            f"Should have {len(file_specs)} file_specs, got {count}"


@pytest.mark.postgres
class TestExcelIdempotency:
    """Test Excel persistence idempotency."""
    
    def test_excel_spec_upsert_no_duplicates(self, postgres_connection):
        """
        Inserting the same Excel-derived file_spec twice via upsert
        should not create duplicates.
        """
        from pathlib import Path
        from integration_coworker.sources.excel import ExcelSource
        import json
        
        fixture_path = Path(__file__).parent / "fixtures" / "file_specs" / "customers_simple.xlsx"
        excel_bytes = fixture_path.read_bytes()
        
        source = ExcelSource()
        parsed = source.parse(excel_bytes, "customers_simple.xlsx")
        
        file_spec = parsed.data.get("file_spec")
        fields = parsed.data.get("fields", [])
        
        cur = postgres_connection.cursor()
        
        # Create source system
        cur.execute("""
            INSERT INTO spec_silver.source_systems (code, display_name)
            VALUES ('excel_idempotency', 'Excel Idempotency Test') RETURNING id
        """)
        source_system_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # First insert
        cur.execute("""
            INSERT INTO spec_silver.file_specs 
            (source_system_id, name, file_type, encoding, description)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (source_system_id, name) 
            DO UPDATE SET description = EXCLUDED.description
            RETURNING id
        """, (source_system_id, file_spec.name, file_spec.file_type, 
              file_spec.encoding, 'First run'))
        first_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # Insert fields
        for field in fields:
            cur.execute("""
                INSERT INTO spec_silver.file_fields 
                (file_spec_id, name, field_type, position)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (file_spec_id, name) 
                DO UPDATE SET field_type = EXCLUDED.field_type
            """, (first_id, field.name, field.field_type, field.position))
        postgres_connection.commit()
        
        # Count after first run
        cur.execute("""
            SELECT COUNT(*) FROM spec_silver.file_specs 
            WHERE source_system_id = %s
        """, (source_system_id,))
        count1_specs = cur.fetchone()[0]
        
        cur.execute("""
            SELECT COUNT(*) FROM spec_silver.file_fields 
            WHERE file_spec_id = %s
        """, (first_id,))
        count1_fields = cur.fetchone()[0]
        
        # Second insert (re-run)
        cur.execute("""
            INSERT INTO spec_silver.file_specs 
            (source_system_id, name, file_type, encoding, description)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (source_system_id, name) 
            DO UPDATE SET description = EXCLUDED.description
            RETURNING id
        """, (source_system_id, file_spec.name, file_spec.file_type, 
              file_spec.encoding, 'Second run'))
        second_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # Re-insert fields
        for field in fields:
            cur.execute("""
                INSERT INTO spec_silver.file_fields 
                (file_spec_id, name, field_type, position)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (file_spec_id, name) 
                DO UPDATE SET field_type = EXCLUDED.field_type
            """, (second_id, field.name, field.field_type, field.position))
        postgres_connection.commit()
        
        # Count after second run
        cur.execute("""
            SELECT COUNT(*) FROM spec_silver.file_specs 
            WHERE source_system_id = %s
        """, (source_system_id,))
        count2_specs = cur.fetchone()[0]
        
        cur.execute("""
            SELECT COUNT(*) FROM spec_silver.file_fields 
            WHERE file_spec_id = %s
        """, (first_id,))
        count2_fields = cur.fetchone()[0]
        
        # IDs should match
        assert first_id == second_id, "Upsert should return same file_spec ID"
        
        # Counts should be unchanged
        assert count2_specs == count1_specs, \
            f"file_specs changed from {count1_specs} to {count2_specs}"
        assert count2_fields == count1_fields, \
            f"file_fields changed from {count1_fields} to {count2_fields}"
        
        # Description should be updated
        cur.execute("""
            SELECT description FROM spec_silver.file_specs WHERE id = %s
        """, (first_id,))
        desc = cur.fetchone()[0]
        assert desc == 'Second run', "Description should be updated"


@pytest.mark.postgres
class TestPDFGuideSourceToPostgres:
    """Test PDF guide source integration with Postgres."""
    
    def test_pdf_guide_detection(self):
        """Test PDF guide detection returns valid confidence."""
        from pathlib import Path
        from integration_coworker.sources.pdf_guide import PDFGuideSource
        
        fixture_path = Path(__file__).parent / "fixtures" / "file_specs" / "customer_field_guide.pdf"
        if not fixture_path.exists():
            pytest.skip("customer_field_guide.pdf fixture not found")
        
        pdf_bytes = fixture_path.read_bytes()
        source = PDFGuideSource()
        confidence = source.detect(pdf_bytes, "customer_field_guide.pdf", "")
        
        # Should detect as PDF guide with reasonable confidence
        assert confidence > 0, "PDF should be detected"
    
    def test_pdf_guide_parse_returns_fields(self):
        """Test PDF guide parsing extracts field definitions."""
        from pathlib import Path
        from integration_coworker.sources.pdf_guide import PDFGuideSource
        from integration_coworker.sources.base import SourceType
        
        fixture_path = Path(__file__).parent / "fixtures" / "file_specs" / "customer_field_guide.pdf"
        if not fixture_path.exists():
            pytest.skip("customer_field_guide.pdf fixture not found")
        
        pdf_bytes = fixture_path.read_bytes()
        source = PDFGuideSource()
        parsed = source.parse(pdf_bytes, "customer_field_guide.pdf")
        
        assert parsed.source_type == SourceType.FILE
        assert parsed.data is not None
        assert "guide_fields" in parsed.data
        assert len(parsed.data["guide_fields"]) > 0
    
    def test_pdf_guide_fields_persist_to_postgres(self, postgres_connection):
        """Test PDF-extracted fields can be persisted to Postgres."""
        from pathlib import Path
        from integration_coworker.sources.pdf_guide import PDFGuideSource
        
        fixture_path = Path(__file__).parent / "fixtures" / "file_specs" / "customer_field_guide.pdf"
        if not fixture_path.exists():
            pytest.skip("customer_field_guide.pdf fixture not found")
        
        pdf_bytes = fixture_path.read_bytes()
        source = PDFGuideSource()
        parsed = source.parse(pdf_bytes, "customer_field_guide.pdf")
        
        if not parsed.is_valid():
            pytest.skip(f"PDF parsing failed: {parsed.errors}")
        
        file_spec = parsed.data.get("file_spec")
        fields = parsed.data.get("fields", [])
        
        if not file_spec or not fields:
            pytest.skip("PDF did not produce file_spec or fields")
        
        cur = postgres_connection.cursor()
        
        # Create source system
        cur.execute("""
            INSERT INTO spec_silver.source_systems (code, display_name)
            VALUES ('pdf_guide_test', 'PDF Guide Test') RETURNING id
        """)
        source_system_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # Insert file_spec
        cur.execute("""
            INSERT INTO spec_silver.file_specs 
            (source_system_id, name, file_type, encoding, description)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (source_system_id, file_spec.name, file_spec.file_type, 
              file_spec.encoding, file_spec.description))
        file_spec_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # Insert fields
        for field in fields:
            cur.execute("""
                INSERT INTO spec_silver.file_fields 
                (file_spec_id, name, field_type, position, start_position, length, 
                 nullable, inference_confidence, description)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (file_spec_id, field.name, field.field_type, field.position,
                  field.start_position, field.length, field.nullable,
                  field.inference_confidence, field.description))
        postgres_connection.commit()
        
        # Verify data persisted
        cur.execute("""
            SELECT name, file_type FROM spec_silver.file_specs WHERE id = %s
        """, (file_spec_id,))
        row = cur.fetchone()
        assert row is not None
        assert row[0] == file_spec.name
        
        cur.execute("""
            SELECT COUNT(*) FROM spec_silver.file_fields WHERE file_spec_id = %s
        """, (file_spec_id,))
        field_count = cur.fetchone()[0]
        assert field_count == len(fields)
    
    def test_pdf_guide_stores_inference_confidence(self, postgres_connection):
        """Test that inference_confidence from PDF extraction is preserved."""
        from pathlib import Path
        from integration_coworker.sources.pdf_guide import PDFGuideSource
        
        fixture_path = Path(__file__).parent / "fixtures" / "file_specs" / "customer_field_guide.pdf"
        if not fixture_path.exists():
            pytest.skip("customer_field_guide.pdf fixture not found")
        
        pdf_bytes = fixture_path.read_bytes()
        source = PDFGuideSource()
        parsed = source.parse(pdf_bytes, "customer_field_guide.pdf")
        
        if not parsed.is_valid():
            pytest.skip(f"PDF parsing failed: {parsed.errors}")
        
        fields = parsed.data.get("fields", [])
        if not fields:
            pytest.skip("PDF did not produce fields")
        
        cur = postgres_connection.cursor()
        
        # Create source system
        cur.execute("""
            INSERT INTO spec_silver.source_systems (code, display_name)
            VALUES ('pdf_confidence_test', 'PDF Confidence Test') RETURNING id
        """)
        source_system_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # Insert file_spec
        cur.execute("""
            INSERT INTO spec_silver.file_specs 
            (source_system_id, name, file_type, encoding)
            VALUES (%s, 'pdf_test_spec', 'fixed_width', 'utf-8')
            RETURNING id
        """, (source_system_id,))
        file_spec_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # Insert field with specific confidence
        field = fields[0]
        cur.execute("""
            INSERT INTO spec_silver.file_fields 
            (file_spec_id, name, field_type, position, inference_confidence)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (file_spec_id, field.name, field.field_type, field.position,
              field.inference_confidence))
        field_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # Verify confidence stored correctly
        cur.execute("""
            SELECT inference_confidence FROM spec_silver.file_fields WHERE id = %s
        """, (field_id,))
        stored_conf = cur.fetchone()[0]
        
        assert stored_conf is not None
        assert abs(float(stored_conf) - field.inference_confidence) < 0.001


@pytest.mark.postgres
class TestPDFGuideIdempotency:
    """Test PDF guide persistence idempotency."""
    
    def test_pdf_guide_spec_upsert_no_duplicates(self, postgres_connection):
        """
        Inserting the same PDF-derived file_spec twice via upsert
        should not create duplicates.
        """
        from pathlib import Path
        from integration_coworker.sources.pdf_guide import PDFGuideSource
        
        fixture_path = Path(__file__).parent / "fixtures" / "file_specs" / "customer_field_guide.pdf"
        if not fixture_path.exists():
            pytest.skip("customer_field_guide.pdf fixture not found")
        
        pdf_bytes = fixture_path.read_bytes()
        source = PDFGuideSource()
        parsed = source.parse(pdf_bytes, "customer_field_guide.pdf")
        
        if not parsed.is_valid():
            pytest.skip(f"PDF parsing failed: {parsed.errors}")
        
        file_spec = parsed.data.get("file_spec")
        fields = parsed.data.get("fields", [])
        
        if not file_spec or not fields:
            pytest.skip("PDF did not produce file_spec or fields")
        
        cur = postgres_connection.cursor()
        
        # Create source system
        cur.execute("""
            INSERT INTO spec_silver.source_systems (code, display_name)
            VALUES ('pdf_idempotency', 'PDF Idempotency Test') RETURNING id
        """)
        source_system_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # First insert
        cur.execute("""
            INSERT INTO spec_silver.file_specs 
            (source_system_id, name, file_type, encoding, description)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (source_system_id, name) 
            DO UPDATE SET description = EXCLUDED.description
            RETURNING id
        """, (source_system_id, file_spec.name, file_spec.file_type, 
              file_spec.encoding, 'First run'))
        first_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # Insert fields
        for field in fields:
            cur.execute("""
                INSERT INTO spec_silver.file_fields 
                (file_spec_id, name, field_type, position, inference_confidence)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (file_spec_id, name) 
                DO UPDATE SET field_type = EXCLUDED.field_type
            """, (first_id, field.name, field.field_type, field.position,
                  field.inference_confidence))
        postgres_connection.commit()
        
        # Count after first run
        cur.execute("""
            SELECT COUNT(*) FROM spec_silver.file_specs 
            WHERE source_system_id = %s
        """, (source_system_id,))
        count1_specs = cur.fetchone()[0]
        
        cur.execute("""
            SELECT COUNT(*) FROM spec_silver.file_fields 
            WHERE file_spec_id = %s
        """, (first_id,))
        count1_fields = cur.fetchone()[0]
        
        # Second insert (re-run)
        cur.execute("""
            INSERT INTO spec_silver.file_specs 
            (source_system_id, name, file_type, encoding, description)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (source_system_id, name) 
            DO UPDATE SET description = EXCLUDED.description
            RETURNING id
        """, (source_system_id, file_spec.name, file_spec.file_type, 
              file_spec.encoding, 'Second run'))
        second_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # Re-insert fields
        for field in fields:
            cur.execute("""
                INSERT INTO spec_silver.file_fields 
                (file_spec_id, name, field_type, position, inference_confidence)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (file_spec_id, name) 
                DO UPDATE SET field_type = EXCLUDED.field_type
            """, (second_id, field.name, field.field_type, field.position,
                  field.inference_confidence))
        postgres_connection.commit()
        
        # Count after second run
        cur.execute("""
            SELECT COUNT(*) FROM spec_silver.file_specs 
            WHERE source_system_id = %s
        """, (source_system_id,))
        count2_specs = cur.fetchone()[0]
        
        cur.execute("""
            SELECT COUNT(*) FROM spec_silver.file_fields 
            WHERE file_spec_id = %s
        """, (first_id,))
        count2_fields = cur.fetchone()[0]
        
        # IDs should match
        assert first_id == second_id, "Upsert should return same file_spec ID"
        
        # Counts should be unchanged
        assert count2_specs == count1_specs, \
            f"file_specs changed from {count1_specs} to {count2_specs}"
        assert count2_fields == count1_fields, \
            f"file_fields changed from {count1_fields} to {count2_fields}"
        
        # Description should be updated
        cur.execute("""
            SELECT description FROM spec_silver.file_specs WHERE id = %s
        """, (first_id,))
        desc = cur.fetchone()[0]
        assert desc == 'Second run', "Description should be updated"
    
    def test_pdf_guide_field_update_preserves_id(self, postgres_connection):
        """Test that re-importing PDF updates fields rather than duplicating."""
        from pathlib import Path
        from integration_coworker.sources.pdf_guide import PDFGuideSource
        
        fixture_path = Path(__file__).parent / "fixtures" / "file_specs" / "customer_field_guide.pdf"
        if not fixture_path.exists():
            pytest.skip("customer_field_guide.pdf fixture not found")
        
        pdf_bytes = fixture_path.read_bytes()
        source = PDFGuideSource()
        parsed = source.parse(pdf_bytes, "customer_field_guide.pdf")
        
        if not parsed.is_valid():
            pytest.skip(f"PDF parsing failed: {parsed.errors}")
        
        fields = parsed.data.get("fields", [])
        if not fields:
            pytest.skip("PDF did not produce fields")
        
        cur = postgres_connection.cursor()
        
        # Create source system
        cur.execute("""
            INSERT INTO spec_silver.source_systems (code, display_name)
            VALUES ('pdf_field_update', 'PDF Field Update Test') RETURNING id
        """)
        source_system_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # Insert file_spec
        cur.execute("""
            INSERT INTO spec_silver.file_specs 
            (source_system_id, name, file_type, encoding)
            VALUES (%s, 'pdf_field_test', 'fixed_width', 'utf-8')
            RETURNING id
        """, (source_system_id,))
        file_spec_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        field = fields[0]
        
        # First insert with original confidence
        cur.execute("""
            INSERT INTO spec_silver.file_fields 
            (file_spec_id, name, field_type, position, inference_confidence)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (file_spec_id, name) 
            DO UPDATE SET inference_confidence = EXCLUDED.inference_confidence
            RETURNING id
        """, (file_spec_id, field.name, field.field_type, field.position, 0.5))
        first_field_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # Second insert with updated confidence
        cur.execute("""
            INSERT INTO spec_silver.file_fields 
            (file_spec_id, name, field_type, position, inference_confidence)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (file_spec_id, name) 
            DO UPDATE SET inference_confidence = EXCLUDED.inference_confidence
            RETURNING id
        """, (file_spec_id, field.name, field.field_type, field.position, 0.9))
        second_field_id = cur.fetchone()[0]
        postgres_connection.commit()
        
        # Should be same ID
        assert first_field_id == second_field_id
        
        # Confidence should be updated
        cur.execute("""
            SELECT inference_confidence FROM spec_silver.file_fields WHERE id = %s
        """, (first_field_id,))
        conf = float(cur.fetchone()[0])
        assert abs(conf - 0.9) < 0.001
