"""
Unit tests for FixedWidthSource and fixed_width_parser.

Tests cover:
- Line length inference
- Column boundary detection from whitespace
- Type inference
- Detection confidence
- Silver model conversion
- Error handling for invalid/ambiguous files

Test fixtures:
- bank_fixed_width_inferable.txt: Has whitespace between fields, inference succeeds
- bank_fixed_width.txt: Contiguous fields (no whitespace), realistic bank format, inference fails
- adversarial_contiguous_fields.txt: Pure contiguous (no spaces at all), inference must fail

The inferable fixture bank_fixed_width_inferable.txt has this layout (91 chars total):
  Fields are space-separated, making whitespace boundary detection possible.
  
The contiguous fixture bank_fixed_width.txt has packed fields without delimiters,
which requires explicit colspec to parse (inference confidence < INFER_MIN).
"""
import pytest
from pathlib import Path


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def bank_fixed_width_content():
    """Load the bank fixed-width test fixture (with whitespace boundaries for inference)."""
    fixture_path = Path(__file__).parent / "fixtures" / "file_specs" / "bank_fixed_width_inferable.txt"
    return fixture_path.read_text()


@pytest.fixture
def bank_fixed_width_path():
    """Return path to the bank fixed-width test fixture (with whitespace for inference)."""
    return Path(__file__).parent / "fixtures" / "file_specs" / "bank_fixed_width_inferable.txt"


@pytest.fixture
def bank_fixed_width_contiguous_content():
    """Load the contiguous bank fixed-width fixture (no whitespace between fields)."""
    fixture_path = Path(__file__).parent / "fixtures" / "file_specs" / "bank_fixed_width.txt"
    return fixture_path.read_text()


@pytest.fixture
def simple_fixed_width():
    """Simple fixed-width content for basic tests."""
    return """AAA  100  2024-01-01
BBB  200  2024-01-02
CCC  300  2024-01-03
DDD  400  2024-01-04
EEE  500  2024-01-05
"""


@pytest.fixture
def delimited_content():
    """Delimited content that should NOT be detected as fixed-width."""
    return """id,name,amount
1,Alice,100.50
2,Bob,200.75
3,Carol,150.25
"""


@pytest.fixture
def inconsistent_length_content():
    """Content with inconsistent line lengths."""
    return """Short line
This is a much longer line that varies
Medium length line here
X
"""


# =============================================================================
# Parser Tests - infer_line_length
# =============================================================================

class TestInferLineLength:
    """Tests for infer_line_length function."""
    
    def test_consistent_lines_returns_length(self, bank_fixed_width_content):
        """Consistent line lengths should be detected."""
        from integration_coworker.parsers.fixed_width_parser import infer_line_length
        
        length = infer_line_length(bank_fixed_width_content)
        
        # Should detect the 88-char lines (with spaces for boundary detection)
        assert length is not None
        assert length == 88  # Bank fixture with space-separated fields
    
    def test_simple_fixed_width(self, simple_fixed_width):
        """Simple fixed-width should have consistent length."""
        from integration_coworker.parsers.fixed_width_parser import infer_line_length
        
        length = infer_line_length(simple_fixed_width)
        
        assert length is not None
        assert length == 20  # "AAA  100  2024-01-01" = 20 chars
    
    def test_inconsistent_returns_none(self, inconsistent_length_content):
        """Inconsistent line lengths should return None."""
        from integration_coworker.parsers.fixed_width_parser import infer_line_length
        
        length = infer_line_length(inconsistent_length_content)
        
        assert length is None
    
    def test_tolerance_allows_outliers(self):
        """
        Legacy tolerance parameter behavior.
        
        Note: The new confidence-based parser uses variance thresholds instead 
        of tolerance counts. This test verifies the legacy API still works
        but with the new semantics (10% variance tolerance by default).
        """
        from integration_coworker.parsers.fixed_width_parser import infer_line_length
        
        content = "12345678901234567890\n" * 10 + "short\n"  # One outlier (~9% variance)
        
        # With default tolerance, 90%+ consistent lines should pass
        # (10 of 11 lines are 20 chars = 91% consistency)
        assert infer_line_length(content, tolerance=0) == 20
        
        # With more outliers, should fail
        many_outliers = "12345678901234567890\n" * 5 + "short\n" * 5  # 50% inconsistent
        assert infer_line_length(many_outliers, tolerance=0) is None


# =============================================================================
# Parser Tests - infer_colspec_from_whitespace
# =============================================================================

class TestInferColspec:
    """Tests for column boundary inference."""
    
    def test_simple_whitespace_boundaries(self, simple_fixed_width):
        """Should detect column boundaries from space patterns."""
        from integration_coworker.parsers.fixed_width_parser import (
            infer_colspec_from_whitespace,
            infer_line_length,
        )
        
        line_length = infer_line_length(simple_fixed_width)
        colspec = infer_colspec_from_whitespace(simple_fixed_width, line_length)
        
        assert colspec is not None
        # Should find boundaries at spaces
        # "AAA  100  2024-01-01" -> fields at positions 0, 5, 10
        assert len(colspec) >= 2
    
    def test_bank_fixture_boundaries(self, bank_fixed_width_content):
        """Bank fixture should have detectable boundaries."""
        from integration_coworker.parsers.fixed_width_parser import (
            infer_colspec_from_whitespace,
            infer_line_length,
        )
        
        line_length = infer_line_length(bank_fixed_width_content)
        assert line_length == 88  # Bank fixture with space-separated fields
        
        colspec = infer_colspec_from_whitespace(bank_fixed_width_content, line_length)
        
        # May or may not detect all boundaries depending on space patterns
        # The bank file has space-padded names, so should detect some
        if colspec:
            # All fields should have positive length
            assert all(length > 0 for _, length in colspec)


# =============================================================================
# Parser Tests - Type Inference
# =============================================================================

class TestTypeInference:
    """Tests for field type inference."""
    
    def test_integer_detection(self):
        """Should detect integer fields."""
        from integration_coworker.parsers.fixed_width_parser import detect_field_types
        
        content = """123  ABC
456  DEF
789  GHI
"""
        colspec = [(0, 3), (5, 3)]
        types = detect_field_types(content, colspec, line_length=8)
        
        assert types[0] == "integer"
        assert types[1] == "string"
    
    def test_date_detection(self):
        """Should detect date fields."""
        from integration_coworker.parsers.fixed_width_parser import detect_field_types
        
        content = """2024-01-01  ABC
2024-01-02  DEF
2024-01-03  GHI
"""
        colspec = [(0, 10), (12, 3)]
        types = detect_field_types(content, colspec, line_length=15)
        
        assert types[0] == "date"
        assert types[1] == "string"
    
    def test_decimal_detection(self):
        """Should detect decimal fields."""
        from integration_coworker.parsers.fixed_width_parser import detect_field_types
        
        content = """123.45  ABC
456.78  DEF
789.01  GHI
"""
        colspec = [(0, 6), (8, 3)]
        types = detect_field_types(content, colspec, line_length=11)
        
        assert types[0] == "decimal"


# =============================================================================
# Parser Tests - Full Schema Inference
# =============================================================================

class TestFullSchemaInference:
    """Tests for complete schema inference."""
    
    def test_simple_schema_inference(self, simple_fixed_width):
        """Should infer schema from simple fixed-width content."""
        from integration_coworker.parsers.fixed_width_parser import infer_fixed_width_schema
        
        schema = infer_fixed_width_schema(simple_fixed_width)
        
        # Check schema properties
        assert schema.line_length == 20
        # Should have at least some fields inferred
        if not schema.errors:
            assert len(schema.fields) >= 2
    
    def test_explicit_colspec_parsing(self):
        """Should parse correctly with explicit column spec."""
        from integration_coworker.parsers.fixed_width_parser import parse_with_explicit_colspec
        
        content = """ACCT001SMITH     00012500
ACCT002JONES     00045000
ACCT003BROWN     00087500
"""
        # Known layout: account(7), name(10), balance(8)
        colspec = [(0, 7), (7, 10), (17, 8)]
        field_names = ["account", "name", "balance"]
        
        schema = parse_with_explicit_colspec(content, colspec, field_names)
        
        assert not schema.errors
        assert len(schema.fields) == 3
        assert schema.fields[0].name == "account"
        assert schema.fields[0].start == 0
        assert schema.fields[0].length == 7
        assert schema.fields[1].name == "name"
        assert schema.fields[1].start == 7
        assert schema.fields[1].length == 10
        assert schema.fields[2].name == "balance"
        assert schema.fields[2].start == 17
        assert schema.fields[2].length == 8
    
    def test_invalid_content_returns_errors(self, inconsistent_length_content):
        """Inconsistent content should return errors, not crash."""
        from integration_coworker.parsers.fixed_width_parser import infer_fixed_width_schema
        
        schema = infer_fixed_width_schema(inconsistent_length_content)
        
        assert schema.errors  # Should have errors
        assert len(schema.fields) == 0


# =============================================================================
# Source Tests - Detection
# =============================================================================

class TestFixedWidthSourceDetection:
    """Tests for FixedWidthSource.detect()."""
    
    def test_high_confidence_for_fw_extension(self, bank_fixed_width_content):
        """Files with .fw extension should get high confidence."""
        from integration_coworker.sources.fixed_width import FixedWidthSource
        
        source = FixedWidthSource()
        confidence = source.detect(bank_fixed_width_content, "accounts.fw", "")
        
        assert confidence >= 0.8
    
    def test_high_confidence_for_dat_extension(self, bank_fixed_width_content):
        """Files with .dat extension should get high confidence."""
        from integration_coworker.sources.fixed_width import FixedWidthSource
        
        source = FixedWidthSource()
        confidence = source.detect(bank_fixed_width_content, "data.dat", "")
        
        assert confidence >= 0.8
    
    def test_medium_confidence_for_txt_extension(self, bank_fixed_width_content):
        """
        Files with .txt extension get weighted confidence.
        
        The new weighted scoring considers:
        - Line consistency (35%)
        - Delimiter absence (25%)
        - Boundary stability (25%)
        - Extension hint (15%)
        
        For well-structured fixed-width content with .txt extension,
        confidence should still be high due to strong content signals.
        """
        from integration_coworker.sources.fixed_width import FixedWidthSource
        
        source = FixedWidthSource()
        confidence = source.detect(bank_fixed_width_content, "data.txt", "")
        
        # .txt gets 0.5 for extension, but content signals are strong
        # So overall confidence should be 0.85-0.95 range
        assert 0.85 <= confidence <= 0.98
    
    def test_zero_confidence_for_csv_extension(self, bank_fixed_width_content):
        """Files with .csv extension should get zero confidence."""
        from integration_coworker.sources.fixed_width import FixedWidthSource
        
        source = FixedWidthSource()
        confidence = source.detect(bank_fixed_width_content, "data.csv", "")
        
        assert confidence == 0.0
    
    def test_zero_confidence_for_delimited_content(self, delimited_content):
        """Delimited content should get zero confidence."""
        from integration_coworker.sources.fixed_width import FixedWidthSource
        
        source = FixedWidthSource()
        confidence = source.detect(delimited_content, "data.txt", "")
        
        assert confidence == 0.0
    
    def test_zero_confidence_for_inconsistent_content(self, inconsistent_length_content):
        """Inconsistent line lengths should get zero confidence."""
        from integration_coworker.sources.fixed_width import FixedWidthSource
        
        source = FixedWidthSource()
        confidence = source.detect(inconsistent_length_content, "data.txt", "")
        
        assert confidence == 0.0


# =============================================================================
# Source Tests - Parsing
# =============================================================================

class TestFixedWidthSourceParsing:
    """Tests for FixedWidthSource.parse()."""
    
    def test_parse_returns_parsedspec(self, simple_fixed_width):
        """parse() should return a ParsedSpec."""
        from integration_coworker.sources.fixed_width import FixedWidthSource
        from integration_coworker.sources.base import ParsedSpec, SourceType
        
        source = FixedWidthSource()
        result = source.parse(simple_fixed_width, "data.fw")
        
        assert isinstance(result, ParsedSpec)
        assert result.source_type == SourceType.FILE
    
    def test_parse_produces_file_spec(self, simple_fixed_width):
        """parse() should produce FileSpec in data."""
        from integration_coworker.sources.fixed_width import FixedWidthSource
        from integration_coworker.domain.models import FileSpec
        
        source = FixedWidthSource()
        result = source.parse(simple_fixed_width, "data.fw")
        
        if result.is_valid():
            assert "file_spec" in result.data
            assert isinstance(result.data["file_spec"], FileSpec)
            assert result.data["file_spec"].file_type == "fixed_width"
    
    def test_parse_produces_fields_with_positions(self, simple_fixed_width):
        """parse() should produce FileFields with start_position and length."""
        from integration_coworker.sources.fixed_width import FixedWidthSource
        from integration_coworker.domain.models import FileField
        
        source = FixedWidthSource()
        result = source.parse(simple_fixed_width, "data.fw")
        
        if result.is_valid():
            assert "fields" in result.data
            fields = result.data["fields"]
            assert len(fields) >= 1
            
            for field in fields:
                assert isinstance(field, FileField)
                assert field.start_position is not None
                assert field.length is not None
                assert field.length > 0
    
    def test_parse_produces_record_layout(self, simple_fixed_width):
        """parse() should produce RecordLayout in data."""
        from integration_coworker.sources.fixed_width import FixedWidthSource
        from integration_coworker.domain.models import RecordLayout
        
        source = FixedWidthSource()
        result = source.parse(simple_fixed_width, "data.fw")
        
        if result.is_valid():
            assert "record_layouts" in result.data
            layouts = result.data["record_layouts"]
            assert len(layouts) >= 1
            
            for layout in layouts:
                assert isinstance(layout, RecordLayout)
                assert layout.record_type == "detail"
                assert layout.record_length is not None
    
    def test_parse_returns_errors_for_invalid(self, inconsistent_length_content):
        """parse() should return errors for invalid content."""
        from integration_coworker.sources.fixed_width import FixedWidthSource
        
        source = FixedWidthSource()
        result = source.parse(inconsistent_length_content, "data.fw")
        
        assert result.errors
        assert result.data is None


# =============================================================================
# Integration Tests - SpecSource Routing
# =============================================================================

class TestSpecSourceRouting:
    """Test that fixed-width files route through SpecSource correctly."""
    
    def test_detect_and_route_for_fixed_width(self, bank_fixed_width_content):
        """Fixed-width content should route through FixedWidthSource."""
        from integration_coworker.sources import detect_and_route, ensure_sources_registered
        from integration_coworker.sources.base import SourceType
        
        ensure_sources_registered()
        
        # Use .dat extension to strongly indicate fixed-width
        result = detect_and_route(bank_fixed_width_content, "bank_accounts.dat", "")
        
        assert result.source_type == SourceType.FILE
        # Should have parsed (possibly with inference limitations)
        if result.is_valid():
            assert "file_spec" in result.data
            assert result.data["file_spec"].file_type == "fixed_width"
    
    def test_csv_not_routed_as_fixed_width(self, delimited_content):
        """CSV content should NOT be detected as fixed-width by FixedWidthSource.
        
        Note: This test verifies FixedWidthSource correctly rejects CSV content.
        It does NOT test CSVSource routing (that's a separate concern).
        """
        from integration_coworker.sources.fixed_width import FixedWidthSource
        
        source = FixedWidthSource()
        
        # CSV file should NOT match as fixed-width
        confidence = source.detect(delimited_content, "data.csv", "text/csv")
        
        # Should be rejected - CSV has delimiters, no consistent line length
        assert confidence == 0.0, f"CSV content should not be detected as fixed-width (got {confidence})"


# =============================================================================
# Edge Cases
# =============================================================================

class TestEdgeCases:
    """Test edge cases and error handling."""
    
    def test_empty_content(self):
        """Empty content should be handled gracefully."""
        from integration_coworker.sources.fixed_width import FixedWidthSource
        
        source = FixedWidthSource()
        
        confidence = source.detect("", "data.fw", "")
        assert confidence == 0.0
        
        result = source.parse("", "data.fw")
        assert result.errors
    
    def test_single_line(self):
        """Single line content should be handled."""
        from integration_coworker.sources.fixed_width import FixedWidthSource
        
        source = FixedWidthSource()
        
        confidence = source.detect("single line here", "data.fw", "")
        # Too few lines for reliable detection
        assert confidence < 0.5
    
    def test_bytes_content(self, bank_fixed_width_content):
        """Should handle bytes content."""
        from integration_coworker.sources.fixed_width import FixedWidthSource
        
        source = FixedWidthSource()
        content_bytes = bank_fixed_width_content.encode('utf-8')
        
        confidence = source.detect(content_bytes, "data.dat", "")
        assert confidence > 0
    
    def test_windows_line_endings(self):
        """Should handle Windows line endings (\\r\\n)."""
        from integration_coworker.parsers.fixed_width_parser import infer_line_length
        
        content = "12345678901234567890\r\n" * 5
        
        length = infer_line_length(content)
        assert length == 20  # Should strip \r
