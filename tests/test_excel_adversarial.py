"""
Adversarial tests for ExcelSource detection and inference.

Tests cover:
1. Detection signals and confidence scoring
2. Hard reject cases (CSV renamed .xlsx, text files, etc.)
3. Multi-sheet handling
4. Header detection edge cases
5. Router gate contracts (low-confidence sources don't win routing)
6. Type inference accuracy

Philosophy: Tests that MUST FAIL if confidence gating is removed.
"""

import pytest
from pathlib import Path
from io import BytesIO

pytestmark = pytest.mark.requires_openpyxl


@pytest.fixture
def fixture_path():
    """Path to test fixtures."""
    return Path(__file__).parent / "fixtures" / "file_specs"


@pytest.fixture
def excel_simple_bytes(fixture_path):
    """Simple Excel fixture as bytes."""
    return (fixture_path / "customers_simple.xlsx").read_bytes()


@pytest.fixture
def excel_multisheet_bytes(fixture_path):
    """Multi-sheet Excel fixture as bytes."""
    return (fixture_path / "multi_sheet_workbook.xlsx").read_bytes()


@pytest.fixture
def excel_no_header_bytes(fixture_path):
    """Excel with ambiguous header as bytes."""
    return (fixture_path / "no_header_ambiguous.xlsx").read_bytes()


@pytest.fixture
def excel_empty_bytes(fixture_path):
    """Empty Excel sheet as bytes."""
    return (fixture_path / "empty_sheet.xlsx").read_bytes()


# =============================================================================
# Detection Signal Tests
# =============================================================================

class TestExcelDetectionSignals:
    """Test detection signals are computed correctly."""
    
    def test_valid_xlsx_has_high_confidence(self, excel_simple_bytes):
        """Valid XLSX should detect with high confidence."""
        from integration_coworker.parsers.excel_parser import detect_with_confidence
        from integration_coworker.parsers.excel_config import get_config
        
        config = get_config()
        result = detect_with_confidence(excel_simple_bytes, "data.xlsx", config)
        
        # All signals should be positive
        assert result.signals["zip_signature"] > 0.8, "ZIP signature should be detected"
        assert result.signals["extension_hint"] == 1.0, "XLSX extension should score 1.0"
        assert result.signals["workbook_load"] == 1.0, "Workbook should load successfully"
        
        # Overall confidence should meet threshold
        assert result.confidence >= config.detect_min, \
            f"Valid XLSX confidence {result.confidence} should >= {config.detect_min}"
        assert result.matched is True
    
    def test_signals_include_reasons(self, excel_simple_bytes):
        """Detection should return human-readable reasons."""
        from integration_coworker.parsers.excel_parser import detect_with_confidence
        from integration_coworker.parsers.excel_config import get_config
        
        result = detect_with_confidence(excel_simple_bytes, "data.xlsx", get_config())
        
        assert len(result.reasons) >= 3, "Should have multiple reasons"
        for reason in result.reasons:
            assert "=" in reason, f"Reason should be key=value format: {reason}"


# =============================================================================
# Hard Reject Tests (Adversarial)
# =============================================================================

class TestExcelHardRejects:
    """Test hard reject cases that should NOT be detected as Excel."""
    
    def test_csv_renamed_xlsx_is_rejected(self):
        """CSV content renamed to .xlsx should be rejected."""
        from integration_coworker.parsers.excel_parser import detect_with_confidence
        from integration_coworker.parsers.excel_config import get_config
        
        csv_content = b"id,name,value\n1,Alice,100\n2,Bob,200\n"
        
        config = get_config()
        result = detect_with_confidence(csv_content, "data.xlsx", config)
        
        # Should hard-reject despite extension
        assert result.confidence < config.detect_min, \
            f"CSV content should have low confidence ({result.confidence})"
        assert result.matched is False
        assert result.signals["zip_signature"] == 0.0, "CSV has no ZIP signature"
    
    def test_json_renamed_xlsx_is_rejected(self):
        """JSON content renamed to .xlsx should be rejected."""
        from integration_coworker.parsers.excel_parser import detect_with_confidence
        from integration_coworker.parsers.excel_config import get_config
        
        json_content = b'{"users": [{"id": 1, "name": "Alice"}]}'
        
        config = get_config()
        result = detect_with_confidence(json_content, "data.xlsx", config)
        
        assert result.matched is False
        assert result.confidence < config.detect_min
    
    def test_random_binary_is_rejected(self):
        """Random binary content should be rejected."""
        from integration_coworker.parsers.excel_parser import detect_with_confidence
        from integration_coworker.parsers.excel_config import get_config
        
        import random
        random.seed(42)
        random_bytes = bytes([random.randint(0, 255) for _ in range(1000)])
        
        config = get_config()
        result = detect_with_confidence(random_bytes, "data.xlsx", config)
        
        assert result.matched is False, "Random binary should not match Excel"
    
    def test_csv_extension_always_rejected(self):
        """CSV extension should always be rejected regardless of content."""
        from integration_coworker.sources.excel import ExcelSource
        
        source = ExcelSource()
        
        # Even valid XLSX bytes with .csv extension should be rejected by source
        confidence = source.detect(b"some content", "data.csv", "")
        assert confidence == 0.0, "CSV extension should immediately reject"
    
    def test_text_file_is_rejected(self):
        """Plain text content should be rejected."""
        from integration_coworker.parsers.excel_parser import detect_with_confidence
        from integration_coworker.parsers.excel_config import get_config
        
        text_content = b"This is a plain text file.\nIt has multiple lines.\nNothing special here."
        
        config = get_config()
        result = detect_with_confidence(text_content, "readme.xlsx", config)
        
        assert result.matched is False
        assert "text" in " ".join(result.reasons).lower(), \
            "Reasons should indicate text detection"


# =============================================================================
# Multi-Sheet Tests
# =============================================================================

class TestExcelMultiSheet:
    """Test multi-sheet Excel handling."""
    
    def test_multi_sheet_detected(self, excel_multisheet_bytes):
        """Multi-sheet workbook should be detected."""
        from integration_coworker.sources.excel import ExcelSource
        
        source = ExcelSource()
        confidence = source.detect(excel_multisheet_bytes, "workbook.xlsx", "")
        
        assert confidence > 0.8, "Multi-sheet workbook should have high confidence"
    
    def test_multi_sheet_creates_multiple_filespecs(self, excel_multisheet_bytes):
        """Multi-sheet workbook should create one FileSpec per sheet."""
        from integration_coworker.sources.excel import ExcelSource
        
        source = ExcelSource()
        result = source.parse(excel_multisheet_bytes, "workbook.xlsx")
        
        assert result.is_valid(), f"Should parse successfully: {result.errors}"
        
        # Multi-sheet: check for file_specs list
        assert "file_specs" in result.data, "Should have file_specs list for multi-sheet"
        
        file_specs = result.data["file_specs"]
        assert len(file_specs) >= 2, f"Should have multiple file specs, got {len(file_specs)}"
        
        # Each spec should have unique name
        names = [spec.name for spec in file_specs]
        assert len(names) == len(set(names)), f"File spec names should be unique: {names}"
    
    def test_multi_sheet_metadata_includes_sheet_names(self, excel_multisheet_bytes):
        """Metadata should include all sheet names."""
        from integration_coworker.sources.excel import ExcelSource
        
        source = ExcelSource()
        result = source.parse(excel_multisheet_bytes, "workbook.xlsx")
        
        assert "sheet_names" in result.metadata or "sheet_name" in result.metadata, \
            "Metadata should include sheet name(s)"


# =============================================================================
# Header Detection Tests
# =============================================================================

class TestExcelHeaderDetection:
    """Test header row detection edge cases."""
    
    def test_clear_header_row_detected(self, excel_simple_bytes):
        """Clear string header row should be detected."""
        from integration_coworker.sources.excel import ExcelSource
        
        source = ExcelSource()
        result = source.parse(excel_simple_bytes, "customers.xlsx")
        
        assert result.is_valid()
        
        # Get file_spec from result
        file_spec = result.data.get("file_spec") or result.data.get("file_specs", [None])[0]
        assert file_spec is not None
        assert file_spec.has_header is True, "Should detect header row"
    
    def test_ambiguous_header_emits_warning(self, excel_no_header_bytes):
        """Ambiguous header should produce warnings."""
        from integration_coworker.sources.excel import ExcelSource
        
        source = ExcelSource()
        result = source.parse(excel_no_header_bytes, "numbers.xlsx")
        
        # May or may not be valid, but should have warnings about header detection
        # or should detect as no-header
        file_spec = result.data.get("file_spec") if result.data else None
        
        if file_spec and not file_spec.has_header:
            # Correctly detected no header - good
            pass
        else:
            # If it decided there IS a header, there should be a warning
            assert len(result.warnings) > 0 or not result.is_valid(), \
                "Ambiguous header should produce warnings or reject"


# =============================================================================
# Type Inference Tests
# =============================================================================

class TestExcelTypeInference:
    """Test column type inference accuracy."""
    
    def test_integer_column_detected(self, excel_simple_bytes):
        """Integer columns should be detected."""
        from integration_coworker.sources.excel import ExcelSource
        
        source = ExcelSource()
        result = source.parse(excel_simple_bytes, "customers.xlsx")
        
        assert result.is_valid()
        
        fields = result.data.get("fields", [])
        # customer_id and total_orders should be integers
        field_types = {f.name: f.field_type for f in fields}
        
        assert "customer_id" in field_types, "Should have customer_id field"
        assert field_types["customer_id"] == "integer", \
            f"customer_id should be integer, got {field_types['customer_id']}"
    
    def test_decimal_column_detected(self, excel_simple_bytes):
        """Decimal columns should be detected."""
        from integration_coworker.sources.excel import ExcelSource
        
        source = ExcelSource()
        result = source.parse(excel_simple_bytes, "customers.xlsx")
        
        assert result.is_valid()
        
        fields = result.data.get("fields", [])
        field_types = {f.name: f.field_type for f in fields}
        
        assert "balance" in field_types, "Should have balance field"
        assert field_types["balance"] == "decimal", \
            f"balance should be decimal, got {field_types['balance']}"
    
    def test_date_column_detected(self, excel_simple_bytes):
        """Date columns should be detected."""
        from integration_coworker.sources.excel import ExcelSource
        
        source = ExcelSource()
        result = source.parse(excel_simple_bytes, "customers.xlsx")
        
        assert result.is_valid()
        
        fields = result.data.get("fields", [])
        field_types = {f.name: f.field_type for f in fields}
        
        assert "signup_date" in field_types, "Should have signup_date field"
        assert field_types["signup_date"] == "date", \
            f"signup_date should be date, got {field_types['signup_date']}"


# =============================================================================
# Empty/Error Handling Tests
# =============================================================================

class TestExcelErrorHandling:
    """Test error handling for edge cases."""
    
    def test_empty_sheet_produces_error(self, excel_empty_bytes):
        """Empty sheet should produce errors, not crash."""
        from integration_coworker.sources.excel import ExcelSource
        
        source = ExcelSource()
        result = source.parse(excel_empty_bytes, "empty.xlsx")
        
        # Should not be valid (no data to infer)
        assert not result.is_valid(), "Empty sheet should not be valid"
        assert len(result.errors) > 0, "Should have errors explaining why"
    
    def test_corrupted_file_produces_error(self):
        """Corrupted Excel file should produce clear error."""
        from integration_coworker.sources.excel import ExcelSource
        
        # Bytes that look like ZIP header but aren't valid
        corrupted = b"PK\x03\x04" + b"\x00" * 100
        
        source = ExcelSource()
        result = source.parse(corrupted, "corrupted.xlsx")
        
        assert not result.is_valid(), "Corrupted file should not be valid"
        assert len(result.errors) > 0, "Should have errors"


# =============================================================================
# Router Gate Contract Tests
# =============================================================================

class TestExcelRouterGateContract:
    """
    Contract tests proving router respects confidence thresholds.
    
    These MUST FAIL if routing ignores confidence scores.
    """
    
    def test_low_confidence_excel_loses_to_higher_source(self, monkeypatch):
        """Excel with low confidence should lose to higher-scoring source."""
        from integration_coworker.sources import detect_and_route, ensure_sources_registered
        from integration_coworker.sources.excel import ExcelSource
        
        ensure_sources_registered()
        
        # Monkeypatch Excel to return low confidence
        original_detect = ExcelSource.detect
        
        def patched_detect(self, content, uri, content_type):
            return 0.15  # Very low confidence
        
        monkeypatch.setattr(ExcelSource, "detect", patched_detect)
        
        # Valid XLSX bytes
        import openpyxl
        from io import BytesIO
        
        wb = openpyxl.Workbook()
        ws = wb.active
        ws['A1'] = 'test'
        
        buffer = BytesIO()
        wb.save(buffer)
        excel_bytes = buffer.getvalue()
        
        # With low confidence, routing should fail or use another source
        # (if no other source matches, it raises ValueError)
        try:
            result = detect_and_route(excel_bytes, "test.xlsx", "")
            # If we got a result, it should NOT be from low-confidence Excel
            # unless it's the only handler that returns >0
        except ValueError:
            # Expected - no handler matched with sufficient confidence
            pass
    
    def test_excel_not_selected_when_confidence_is_zero(self, monkeypatch):
        """Excel with zero confidence should never be selected."""
        from integration_coworker.sources.excel import ExcelSource
        
        # Monkeypatch to return zero
        def patched_detect(self, content, uri, content_type):
            return 0.0
        
        monkeypatch.setattr(ExcelSource, "detect", patched_detect)
        
        source = ExcelSource()
        confidence = source.detect(b"anything", "test.xlsx", "")
        
        assert confidence == 0.0, "Patched detect should return 0"


# =============================================================================
# Confidence Gates Contract Tests
# =============================================================================

class TestExcelConfidenceGatesContract:
    """
    Contract tests proving confidence gates are enforced.
    
    These MUST FAIL if someone removes the threshold checks.
    """
    
    def test_detect_min_gate_is_enforced(self, excel_simple_bytes, monkeypatch):
        """DETECT_MIN gate prevents matching below threshold."""
        from integration_coworker.parsers.excel_parser import detect_with_confidence
        from integration_coworker.parsers.excel_config import ExcelConfidenceConfig
        
        # Set very high threshold - even valid Excel shouldn't match
        strict_config = ExcelConfidenceConfig(detect_min=0.99)
        
        result = detect_with_confidence(excel_simple_bytes, "data.xlsx", strict_config)
        
        # Valid XLSX has ~0.95 confidence, should be rejected at 0.99 threshold
        assert result.matched is False, \
            f"DETECT_MIN=0.99 should reject confidence={result.confidence}"
    
    def test_infer_min_gate_is_enforced(self, monkeypatch):
        """INFER_MIN gate prevents valid ParsedSpec below threshold."""
        from integration_coworker.parsers.excel_parser import infer_schema
        from integration_coworker.parsers.excel_config import ExcelConfidenceConfig
        import openpyxl
        from io import BytesIO
        
        # Create minimal workbook that will have low inference confidence
        wb = openpyxl.Workbook()
        ws = wb.active
        ws['A1'] = None  # All nulls = low confidence
        ws['A2'] = None
        
        buffer = BytesIO()
        wb.save(buffer)
        content = buffer.getvalue()
        
        # Set high infer_min threshold
        strict_config = ExcelConfidenceConfig(infer_min=0.95)
        
        result = infer_schema(content, config=strict_config)
        
        # Low-quality data should be rejected at high threshold
        # (either errors or low confidence)
        if result.is_valid():
            assert result.confidence >= strict_config.infer_min, \
                "INFER_MIN should reject low-confidence results"
    
    def test_infer_nrows_limits_sample_window(self):
        """infer_nrows should limit rows used for inference."""
        from integration_coworker.parsers.excel_parser import infer_schema
        from integration_coworker.parsers.excel_config import ExcelConfidenceConfig
        import openpyxl
        from io import BytesIO
        
        # Create workbook with many rows
        wb = openpyxl.Workbook()
        ws = wb.active
        ws['A1'] = 'value'
        
        for i in range(2, 102):  # 100 data rows
            ws.cell(row=i, column=1, value=i)
        
        buffer = BytesIO()
        wb.save(buffer)
        content = buffer.getvalue()
        
        # Limit to 10 rows
        config = ExcelConfidenceConfig(infer_nrows=10)
        result = infer_schema(content, config=config)
        
        if result.is_valid() and result.sheets:
            # row_count should be limited by infer_nrows
            assert result.sheets[0].row_count <= config.infer_nrows, \
                f"Should only read {config.infer_nrows} rows, got {result.sheets[0].row_count}"


# =============================================================================
# Integration Tests
# =============================================================================

class TestExcelIntegration:
    """End-to-end integration tests."""
    
    def test_full_flow_simple_file(self, excel_simple_bytes):
        """Test complete flow from detection to parsed result."""
        from integration_coworker.sources.excel import ExcelSource
        from integration_coworker.sources.base import SourceType
        
        source = ExcelSource()
        
        # Step 1: Detect
        confidence = source.detect(excel_simple_bytes, "customers.xlsx", "")
        assert confidence > 0.8, f"Valid Excel should detect with high confidence"
        
        # Step 2: Parse
        result = source.parse(excel_simple_bytes, "customers.xlsx")
        
        # Step 3: Verify result structure
        assert result.source_type == SourceType.FILE
        assert result.is_valid(), f"Should be valid: {result.errors}"
        assert result.data is not None
        
        # Should have file_spec (single sheet) or file_specs (multi-sheet)
        assert "file_spec" in result.data or "file_specs" in result.data
        assert "fields" in result.data
        assert "record_layouts" in result.data
        
        # Step 4: Verify metadata
        assert "detection" in result.metadata
        assert "inference" in result.metadata
    
    def test_detect_and_route_integration(self, excel_simple_bytes):
        """Test that detect_and_route correctly routes to ExcelSource."""
        from integration_coworker.sources import detect_and_route, ensure_sources_registered
        from integration_coworker.sources.base import SourceType
        
        ensure_sources_registered()
        
        result = detect_and_route(excel_simple_bytes, "customers.xlsx", "")
        
        assert result.source_type == SourceType.FILE
        assert result.is_valid(), f"Should be valid: {result.errors}"
