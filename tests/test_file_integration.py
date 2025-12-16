"""
Integration tests for file integration end-to-end flow.

Tests the full pipeline: CSV sample -> detect -> parse -> Silver Model -> DB persist -> codegen
"""
import os
import pytest
import tempfile
import sqlite3
from pathlib import Path

from integration_coworker.sources.csv_source import CSVSource
from integration_coworker.sources import register_source, detect_and_route, SOURCE_REGISTRY
from integration_coworker.codegen.file_templates import generate_csv_parser
from integration_coworker.domain.models import FileSpec, FileField


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "file_specs"


@pytest.fixture(scope="module")
def sqlite_db():
    """Create a temporary SQLite database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    # Initialize the database schema
    conn = sqlite3.connect(db_path)
    
    # Create all necessary tables
    conn.executescript("""
        -- Source systems table
        CREATE TABLE IF NOT EXISTS source_systems (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            base_url TEXT,
            spec_type TEXT DEFAULT 'openapi',
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        -- File specs table
        CREATE TABLE IF NOT EXISTS file_specs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_system_id INTEGER,
            spec_document_id INTEGER,
            name TEXT NOT NULL,
            file_type TEXT NOT NULL,
            encoding TEXT DEFAULT 'utf-8',
            delimiter TEXT,
            has_header INTEGER DEFAULT 1,
            line_terminator TEXT DEFAULT '\n',
            quote_char TEXT DEFAULT '"',
            escape_char TEXT,
            description TEXT,
            version TEXT,
            sample_uri TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (source_system_id) REFERENCES source_systems(id)
        );
        
        -- File fields table
        CREATE TABLE IF NOT EXISTS file_fields (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_spec_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            field_type TEXT NOT NULL,
            position INTEGER NOT NULL,
            start_position INTEGER,
            length INTEGER,
            format_mask TEXT,
            nullable INTEGER DEFAULT 1,
            default_value TEXT,
            validation_regex TEXT,
            description TEXT,
            sample_values TEXT,
            inference_confidence REAL DEFAULT 1.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (file_spec_id) REFERENCES file_specs(id)
        );
        
        -- Record layouts table
        CREATE TABLE IF NOT EXISTS record_layouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_spec_id INTEGER NOT NULL,
            record_type TEXT NOT NULL,
            identifier_field TEXT,
            identifier_value TEXT,
            record_length INTEGER,
            position INTEGER DEFAULT 0,
            min_occurrences INTEGER DEFAULT 0,
            max_occurrences INTEGER,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (file_spec_id) REFERENCES file_specs(id)
        );
        
        -- File validation rules table
        CREATE TABLE IF NOT EXISTS file_validation_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_spec_id INTEGER NOT NULL,
            field_name TEXT,
            rule_type TEXT NOT NULL,
            rule_config TEXT,
            error_message TEXT,
            severity TEXT DEFAULT 'error',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (file_spec_id) REFERENCES file_specs(id)
        );
    """)
    
    # Insert a test source system
    conn.execute("""
        INSERT INTO source_systems (code, name, spec_type)
        VALUES ('test_file_system', 'Test File System', 'file')
    """)
    conn.commit()
    conn.close()
    
    yield db_path
    
    # Cleanup
    os.unlink(db_path)


class TestCSVEndToEndFlow:
    """Test full CSV processing pipeline."""
    
    def test_csv_detect_parse_persist_codegen(self, sqlite_db):
        """
        Full end-to-end test:
        1. Detect CSV file type
        2. Parse CSV to extract schema
        3. Convert to Silver model
        4. Persist to SQLite
        5. Generate parser code
        6. Verify generated code compiles
        """
        csv_path = FIXTURES_DIR / "sample_customers.csv"
        content = csv_path.read_text(encoding="utf-8")
        
        # Step 1: Detect
        source = CSVSource()
        score = source.detect(content, str(csv_path), "text/plain")
        assert score >= 0.9, f"Expected high detection confidence, got {score}"
        
        # Step 2: Parse
        parsed = source.parse(content, str(csv_path))
        assert parsed.data is not None
        assert parsed.errors is None or len(parsed.errors) == 0
        
        file_spec = parsed.data["file_spec"]
        fields = parsed.data["fields"]
        
        assert file_spec.name == "sample_customers"
        assert len(fields) == 5
        
        # Step 3: Persist to SQLite
        conn = sqlite3.connect(sqlite_db)
        cursor = conn.cursor()
        
        # Get source system ID
        cursor.execute("SELECT id FROM source_systems WHERE code = 'test_file_system'")
        source_system_id = cursor.fetchone()[0]
        
        # Insert file_spec
        cursor.execute("""
            INSERT INTO file_specs 
            (source_system_id, name, file_type, delimiter, has_header, description)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            source_system_id,
            file_spec.name,
            file_spec.file_type,
            file_spec.delimiter,
            1 if file_spec.has_header else 0,
            file_spec.description,
        ))
        file_spec_id = cursor.lastrowid
        
        # Insert file_fields
        for field in fields:
            cursor.execute("""
                INSERT INTO file_fields
                (file_spec_id, name, field_type, position, nullable, inference_confidence)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                file_spec_id,
                field.name,
                field.field_type,
                field.position,
                1 if field.nullable else 0,
                field.inference_confidence,
            ))
        
        conn.commit()
        
        # Verify persistence
        cursor.execute("SELECT COUNT(*) FROM file_specs WHERE name = 'sample_customers'")
        assert cursor.fetchone()[0] == 1
        
        cursor.execute("SELECT COUNT(*) FROM file_fields WHERE file_spec_id = ?", (file_spec_id,))
        assert cursor.fetchone()[0] == 5
        
        conn.close()
        
        # Step 4: Generate parser code
        # Create FileSpec and FileField with IDs from DB
        db_file_spec = FileSpec(
            id=file_spec_id,
            source_system_id=source_system_id,
            name=file_spec.name,
            file_type=file_spec.file_type,
            delimiter=file_spec.delimiter,
            has_header=file_spec.has_header,
        )
        
        db_fields = [
            FileField(
                id=i + 1,
                file_spec_id=file_spec_id,
                name=f.name,
                field_type=f.field_type,
                position=f.position,
                nullable=f.nullable,
            )
            for i, f in enumerate(fields)
        ]
        
        code = generate_csv_parser(db_file_spec, db_fields)
        
        # Step 5: Verify generated code compiles
        compiled = compile(code, "<generated>", "exec")
        assert compiled is not None
        
        # Execute to verify classes/functions are defined
        namespace = {}
        exec(code, namespace)
        
        assert "SampleCustomers" in namespace
        assert "parse_sample_customers" in namespace
        assert "validate_sample_customers" in namespace
        
        # Step 6: Verify the parser can be used (mock parse)
        parser_class = namespace["SampleCustomers"]
        assert hasattr(parser_class, "__dataclass_fields__")

    def test_tsv_detect_parse_persist_codegen(self, sqlite_db):
        """Test TSV file processing end-to-end."""
        tsv_path = FIXTURES_DIR / "sample_orders.tsv"
        content = tsv_path.read_text(encoding="utf-8")
        
        # Detect
        source = CSVSource()
        score = source.detect(content, str(tsv_path), "text/plain")
        assert score >= 0.9
        
        # Parse
        parsed = source.parse(content, str(tsv_path))
        assert parsed.data is not None
        
        file_spec = parsed.data["file_spec"]
        fields = parsed.data["fields"]
        
        assert file_spec.delimiter == "\t"
        assert "tsv" in file_spec.file_type.lower()
        
        # Generate code
        code = generate_csv_parser(file_spec, fields)
        
        # Verify code compiles
        compiled = compile(code, "<generated>", "exec")
        assert compiled is not None


class TestGeneratedParserExecution:
    """Test that generated parsers can actually parse files."""
    
    def test_generated_parser_parses_sample_file(self, tmp_path):
        """Test that generated parser can parse the original sample file."""
        csv_path = FIXTURES_DIR / "sample_customers.csv"
        content = csv_path.read_text(encoding="utf-8")
        
        # Parse CSV
        source = CSVSource()
        parsed = source.parse(content, str(csv_path))
        file_spec = parsed.data["file_spec"]
        fields = parsed.data["fields"]
        
        # Generate parser
        code = generate_csv_parser(file_spec, fields)
        
        # Execute code to get parser function
        namespace = {}
        exec(code, namespace)
        
        parse_func = namespace["parse_sample_customers"]
        
        # Copy sample file to temp location
        test_file = tmp_path / "test_customers.csv"
        test_file.write_text(content)
        
        # Parse with generated parser
        records = parse_func(str(test_file))
        
        assert len(records) == 5
        assert records[0].id == 1
        assert records[0].name == "Alice Johnson"
    
    def test_generated_validator_finds_no_errors_on_valid_data(self, tmp_path):
        """Test validator returns no errors for valid data."""
        csv_path = FIXTURES_DIR / "sample_customers.csv"
        content = csv_path.read_text(encoding="utf-8")
        
        source = CSVSource()
        parsed = source.parse(content, str(csv_path))
        file_spec = parsed.data["file_spec"]
        fields = parsed.data["fields"]
        
        code = generate_csv_parser(file_spec, fields)
        
        namespace = {}
        exec(code, namespace)
        
        test_file = tmp_path / "test_customers.csv"
        test_file.write_text(content)
        
        records = namespace["parse_sample_customers"](str(test_file))
        errors = namespace["validate_sample_customers"](records)
        
        # Valid data should have no errors
        assert len(errors) == 0


class TestDetectAndRouteIntegration:
    """Test source registry and routing."""
    
    def test_detect_and_route_finds_csv_source(self):
        """Test that detect_and_route correctly routes to CSVSource and parses."""
        # Ensure source is registered
        register_source(CSVSource())
        
        csv_path = FIXTURES_DIR / "sample_customers.csv"
        content = csv_path.read_text(encoding="utf-8")
        
        result = detect_and_route(content, str(csv_path), "text/plain")
        
        # detect_and_route returns ParsedSpec (the result of parsing)
        assert result is not None
        from integration_coworker.sources.base import ParsedSpec
        assert isinstance(result, ParsedSpec)
        assert result.data is not None
        assert "file_spec" in result.data
    
    def test_detect_and_route_tsv(self):
        """Test routing for TSV files."""
        register_source(CSVSource())
        
        tsv_path = FIXTURES_DIR / "sample_orders.tsv"
        content = tsv_path.read_text(encoding="utf-8")
        
        result = detect_and_route(content, str(tsv_path), "text/plain")
        
        assert result is not None


class TestDatabaseRoundTrip:
    """Test persisting and retrieving file specs."""
    
    def test_persist_and_retrieve_file_spec(self, sqlite_db):
        """Test persisting a FileSpec and retrieving it."""
        conn = sqlite3.connect(sqlite_db)
        cursor = conn.cursor()
        
        # Insert
        cursor.execute("""
            INSERT INTO file_specs (source_system_id, name, file_type, delimiter, has_header)
            VALUES (1, 'test_roundtrip', 'csv', ',', 1)
        """)
        spec_id = cursor.lastrowid
        conn.commit()
        
        # Retrieve
        cursor.execute("""
            SELECT id, name, file_type, delimiter, has_header
            FROM file_specs WHERE id = ?
        """, (spec_id,))
        
        row = cursor.fetchone()
        assert row is not None
        assert row[1] == "test_roundtrip"
        assert row[2] == "csv"
        assert row[3] == ","
        assert row[4] == 1
        
        conn.close()
    
    def test_persist_and_retrieve_file_fields(self, sqlite_db):
        """Test persisting FileFields and retrieving them."""
        conn = sqlite3.connect(sqlite_db)
        cursor = conn.cursor()
        
        # Insert spec
        cursor.execute("""
            INSERT INTO file_specs (source_system_id, name, file_type)
            VALUES (1, 'test_fields', 'csv')
        """)
        spec_id = cursor.lastrowid
        
        # Insert fields
        fields_data = [
            ("id", "integer", 0),
            ("name", "string", 1),
            ("amount", "decimal", 2),
        ]
        
        for name, ftype, pos in fields_data:
            cursor.execute("""
                INSERT INTO file_fields (file_spec_id, name, field_type, position)
                VALUES (?, ?, ?, ?)
            """, (spec_id, name, ftype, pos))
        
        conn.commit()
        
        # Retrieve
        cursor.execute("""
            SELECT name, field_type, position FROM file_fields
            WHERE file_spec_id = ? ORDER BY position
        """, (spec_id,))
        
        rows = cursor.fetchall()
        assert len(rows) == 3
        assert rows[0] == ("id", "integer", 0)
        assert rows[1] == ("name", "string", 1)
        assert rows[2] == ("amount", "decimal", 2)
        
        conn.close()


class TestMultipleFileFormats:
    """Test handling multiple file formats."""
    
    def test_process_multiple_csv_files(self, sqlite_db):
        """Test processing multiple CSV files in sequence."""
        csv_files = [
            FIXTURES_DIR / "sample_customers.csv",
            FIXTURES_DIR / "sample_orders.tsv",
            FIXTURES_DIR / "sample_products.csv",
        ]
        
        source = CSVSource()
        
        for csv_path in csv_files:
            content = csv_path.read_text(encoding="utf-8")
            
            # Detect
            score = source.detect(content, str(csv_path), "text/plain")
            assert score > 0.5, f"Failed to detect {csv_path.name}"
            
            # Parse
            parsed = source.parse(content, str(csv_path))
            assert parsed.data is not None, f"Failed to parse {csv_path.name}"
            
            # Generate code
            code = generate_csv_parser(
                parsed.data["file_spec"],
                parsed.data["fields"]
            )
            
            # Verify compiles
            compile(code, f"<{csv_path.name}>", "exec")
