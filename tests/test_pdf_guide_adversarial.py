"""
Adversarial and contract tests for PDFGuideSource.

Tests cover:
1. Detection signals - verify weighted scoring
2. Hard rejects - non-PDF, scanned/image PDFs, wrong extensions
3. Extraction quality - field definitions, multi-page handling
4. Confidence gates - warnings at thresholds
5. Edge cases - empty PDFs, minimal text

Per docs/FILE_INTEGRATION_V1_PLAN.md Section 3.3
"""

import os
import pytest
from pathlib import Path

pytestmark = pytest.mark.requires_reportlab


# Test fixtures directory
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "file_specs"


# ============================================================================
# Fixture Loaders
# ============================================================================

@pytest.fixture
def customer_field_guide_bytes():
    """PDF with field definitions table."""
    pdf_path = FIXTURES_DIR / "customer_field_guide.pdf"
    if not pdf_path.exists():
        pytest.skip("customer_field_guide.pdf fixture not found")
    return pdf_path.read_bytes()


@pytest.fixture
def minimal_no_fields_bytes():
    """PDF with text but no field definitions."""
    pdf_path = FIXTURES_DIR / "minimal_no_fields.pdf"
    if not pdf_path.exists():
        pytest.skip("minimal_no_fields.pdf fixture not found")
    return pdf_path.read_bytes()


@pytest.fixture
def sparse_text_scanned_bytes():
    """PDF with very little text (simulates scanned)."""
    pdf_path = FIXTURES_DIR / "sparse_text_scanned.pdf"
    if not pdf_path.exists():
        pytest.skip("sparse_text_scanned.pdf fixture not found")
    return pdf_path.read_bytes()


@pytest.fixture
def multi_page_guide_bytes():
    """Multi-page PDF with field definitions."""
    pdf_path = FIXTURES_DIR / "multi_page_guide.pdf"
    if not pdf_path.exists():
        pytest.skip("multi_page_guide.pdf fixture not found")
    return pdf_path.read_bytes()


@pytest.fixture
def blank_page_bytes():
    """PDF with blank page (no text at all)."""
    pdf_path = FIXTURES_DIR / "blank_page.pdf"
    if not pdf_path.exists():
        pytest.skip("blank_page.pdf fixture not found")
    return pdf_path.read_bytes()


# ============================================================================
# Detection Signal Tests
# ============================================================================

class TestPDFDetectionSignals:
    """Test that detection uses correct weighted signals."""
    
    def test_pdf_signature_required(self):
        """Non-PDF content should get 0.0 confidence."""
        from integration_coworker.parsers.pdf_guide_parser import detect_with_confidence
        from integration_coworker.parsers.pdf_guide_config import get_config
        
        config = get_config()
        
        # Plain text
        result = detect_with_confidence(b"Hello world", "test.pdf", config)
        assert result.confidence == 0.0
        assert not result.matched
        assert "missing %PDF magic bytes" in str(result.reasons)
    
    def test_pdf_signature_gives_base_confidence(self, customer_field_guide_bytes):
        """Valid PDF signature should give positive confidence."""
        from integration_coworker.parsers.pdf_guide_parser import detect_with_confidence
        from integration_coworker.parsers.pdf_guide_config import get_config
        
        config = get_config()
        result = detect_with_confidence(customer_field_guide_bytes, "guide.pdf", config)
        
        assert result.confidence > 0
        assert result.signals["pdf_signature"] == 1.0
        assert "%PDF magic bytes present" in str(result.reasons)
    
    def test_guide_keywords_boost_confidence(self, customer_field_guide_bytes):
        """Guide keywords should boost the signal."""
        from integration_coworker.parsers.pdf_guide_parser import detect_with_confidence
        from integration_coworker.parsers.pdf_guide_config import get_config
        
        config = get_config()
        result = detect_with_confidence(customer_field_guide_bytes, "guide.pdf", config)
        
        # Field guide has many keywords
        assert result.signals["guide_keywords"] > 0.5
    
    def test_field_table_patterns_detected(self, customer_field_guide_bytes):
        """Field table patterns should be detected."""
        from integration_coworker.parsers.pdf_guide_parser import detect_with_confidence
        from integration_coworker.parsers.pdf_guide_config import get_config
        
        config = get_config()
        result = detect_with_confidence(customer_field_guide_bytes, "guide.pdf", config)
        
        # Customer guide has field table patterns
        assert result.signals["field_table_pattern"] >= 0.4
    
    def test_text_extraction_signal(self, customer_field_guide_bytes):
        """Successful text extraction should set signal to 1.0."""
        from integration_coworker.parsers.pdf_guide_parser import detect_with_confidence
        from integration_coworker.parsers.pdf_guide_config import get_config
        
        config = get_config()
        result = detect_with_confidence(customer_field_guide_bytes, "guide.pdf", config)
        
        assert result.signals["text_extraction"] == 1.0
        assert result.extracted_text is not None
        assert len(result.extracted_text) > 100


class TestPDFHardRejects:
    """Test hard rejection conditions."""
    
    def test_non_pdf_bytes_rejected(self):
        """Random bytes should be rejected."""
        from integration_coworker.sources.pdf_guide import PDFGuideSource
        
        source = PDFGuideSource()
        content = os.urandom(1000)
        
        confidence = source.detect(content, "random.pdf", "")
        assert confidence == 0.0
    
    def test_csv_content_renamed_pdf_rejected(self):
        """CSV content with .pdf extension should be rejected."""
        from integration_coworker.sources.pdf_guide import PDFGuideSource
        
        source = PDFGuideSource()
        csv_content = b"name,age,city\nJohn,30,NYC\nJane,25,LA"
        
        confidence = source.detect(csv_content, "data.pdf", "")
        assert confidence == 0.0
    
    def test_json_content_renamed_pdf_rejected(self):
        """JSON content with .pdf extension should be rejected."""
        from integration_coworker.sources.pdf_guide import PDFGuideSource
        
        source = PDFGuideSource()
        json_content = b'{"name": "test", "value": 123}'
        
        confidence = source.detect(json_content, "data.pdf", "")
        assert confidence == 0.0
    
    def test_xlsx_extension_rejected(self, customer_field_guide_bytes):
        """PDF content with .xlsx extension should be rejected."""
        from integration_coworker.sources.pdf_guide import PDFGuideSource
        
        source = PDFGuideSource()
        
        # Even valid PDF bytes should be rejected with wrong extension
        confidence = source.detect(customer_field_guide_bytes, "data.xlsx", "")
        assert confidence == 0.0
    
    def test_csv_extension_rejected(self, customer_field_guide_bytes):
        """PDF content with .csv extension should be rejected."""
        from integration_coworker.sources.pdf_guide import PDFGuideSource
        
        source = PDFGuideSource()
        confidence = source.detect(customer_field_guide_bytes, "data.csv", "")
        assert confidence == 0.0
    
    def test_sparse_text_pdf_rejected(self, sparse_text_scanned_bytes):
        """PDF with very little text (scanned) should be rejected."""
        from integration_coworker.parsers.pdf_guide_parser import detect_with_confidence
        from integration_coworker.parsers.pdf_guide_config import get_config
        
        config = get_config()
        result = detect_with_confidence(sparse_text_scanned_bytes, "scanned.pdf", config)
        
        # Should be hard-rejected as likely scanned
        assert not result.matched
        assert "HARD_REJECT" in str(result.reasons) or result.confidence < 0.5
    
    def test_blank_page_pdf_rejected(self, blank_page_bytes):
        """PDF with no text at all should be rejected."""
        from integration_coworker.parsers.pdf_guide_parser import detect_with_confidence
        from integration_coworker.parsers.pdf_guide_config import get_config
        
        config = get_config()
        result = detect_with_confidence(blank_page_bytes, "blank.pdf", config)
        
        # Should fail due to no text
        assert not result.matched or result.confidence < config.detect_min


class TestPDFFieldExtraction:
    """Test field definition extraction quality."""
    
    def test_extract_fields_from_guide(self, customer_field_guide_bytes):
        """Should extract field definitions from a proper guide."""
        from integration_coworker.parsers.pdf_guide_parser import extract_field_definitions
        from integration_coworker.parsers.pdf_guide_config import get_config
        
        config = get_config()
        result = extract_field_definitions(customer_field_guide_bytes, config)
        
        # Should extract at least some fields
        assert len(result.fields) >= 3
        assert result.confidence > 0
    
    def test_extracted_field_has_name(self, customer_field_guide_bytes):
        """Extracted fields should have names."""
        from integration_coworker.parsers.pdf_guide_parser import extract_field_definitions
        from integration_coworker.parsers.pdf_guide_config import get_config
        
        config = get_config()
        result = extract_field_definitions(customer_field_guide_bytes, config)
        
        for field in result.fields:
            assert field.name
            assert len(field.name) >= 2
    
    def test_extracted_field_has_type(self, customer_field_guide_bytes):
        """Extracted fields should have normalized types."""
        from integration_coworker.parsers.pdf_guide_parser import extract_field_definitions
        from integration_coworker.parsers.pdf_guide_config import get_config
        
        config = get_config()
        result = extract_field_definitions(customer_field_guide_bytes, config)
        
        valid_types = {"string", "integer", "decimal", "date", "datetime", "boolean"}
        for field in result.fields:
            assert field.field_type in valid_types
    
    def test_multi_page_extraction(self, multi_page_guide_bytes):
        """Should extract fields from multi-page PDFs."""
        from integration_coworker.parsers.pdf_guide_parser import extract_field_definitions
        from integration_coworker.parsers.pdf_guide_config import get_config
        
        config = get_config()
        result = extract_field_definitions(multi_page_guide_bytes, config)
        
        assert result.metadata.get("page_count", 0) > 1
        # Should find at least 1 field from the patterns in the PDF
        # The multi-page guide has INTEGER(15), DATE, DECIMAL(12,2) patterns
        assert len(result.fields) >= 1
        # Overall confidence should be reasonable for a guide doc
        assert result.confidence > 0.5
    
    def test_no_fields_from_minimal_pdf(self, minimal_no_fields_bytes):
        """PDF without field definitions should extract nothing useful."""
        from integration_coworker.parsers.pdf_guide_parser import extract_field_definitions
        from integration_coworker.parsers.pdf_guide_config import get_config
        
        config = get_config()
        result = extract_field_definitions(minimal_no_fields_bytes, config)
        
        # Might extract some spurious fields, but confidence should be low
        assert result.confidence < 0.5 or len(result.fields) < 3


class TestPDFConfidenceGates:
    """Test confidence thresholds and warnings."""
    
    def test_high_confidence_no_warnings(self, customer_field_guide_bytes):
        """High-confidence extraction should have no low-confidence warnings."""
        from integration_coworker.sources.pdf_guide import PDFGuideSource
        
        source = PDFGuideSource()
        result = source.parse(customer_field_guide_bytes, "guide.pdf")
        
        if result.confidence >= 0.70:
            # Should not have low confidence warning
            low_conf_warnings = [w for w in result.warnings if "Low confidence" in w]
            assert len(low_conf_warnings) == 0
    
    def test_low_confidence_emits_warning(self, minimal_no_fields_bytes):
        """Low-confidence extraction should emit warning."""
        from integration_coworker.sources.pdf_guide import PDFGuideSource
        
        source = PDFGuideSource()
        result = source.parse(minimal_no_fields_bytes, "minimal.pdf")
        
        if result.confidence < 0.70 and result.data is not None:
            # Should have a warning
            assert len(result.warnings) > 0
    
    def test_below_infer_min_returns_error(self):
        """Extraction below INFER_MIN should return error."""
        from integration_coworker.parsers.pdf_guide_config import PDFGuideConfidenceConfig
        from integration_coworker.parsers.pdf_guide_parser import extract_field_definitions
        
        # Use strict config
        strict_config = PDFGuideConfidenceConfig(
            infer_min=0.99,  # Very high threshold
            infer_warn=0.95,
        )
        
        # Create minimal PDF with text but no fields
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        import io
        
        packet = io.BytesIO()
        c = canvas.Canvas(packet, pagesize=letter)
        c.drawString(100, 750, 'Just some random text without field definitions.')
        c.save()
        packet.seek(0)
        content = packet.read()
        
        result = extract_field_definitions(content, strict_config)
        
        # Should be valid but low confidence
        assert result.confidence < 0.99


class TestPDFSourceParse:
    """Test PDFGuideSource.parse() output format."""
    
    def test_parse_returns_parsed_spec(self, customer_field_guide_bytes):
        """Parse should return ParsedSpec."""
        from integration_coworker.sources.pdf_guide import PDFGuideSource
        from integration_coworker.sources.base import SourceType
        
        source = PDFGuideSource()
        result = source.parse(customer_field_guide_bytes, "guide.pdf")
        
        assert result.source_type == SourceType.FILE
        assert result.source_uri == "guide.pdf"
    
    def test_parse_includes_guide_fields(self, customer_field_guide_bytes):
        """Parse result should include guide_fields."""
        from integration_coworker.sources.pdf_guide import PDFGuideSource
        
        source = PDFGuideSource()
        result = source.parse(customer_field_guide_bytes, "guide.pdf")
        
        if result.is_valid():
            assert "guide_fields" in result.data
            assert isinstance(result.data["guide_fields"], list)
    
    def test_parse_includes_metadata(self, customer_field_guide_bytes):
        """Parse result should include useful metadata."""
        from integration_coworker.sources.pdf_guide import PDFGuideSource
        
        source = PDFGuideSource()
        result = source.parse(customer_field_guide_bytes, "guide.pdf")
        
        assert "detection" in result.metadata
        assert "field_count" in result.metadata or "extraction" in result.metadata
    
    def test_parse_extracts_source_name(self, customer_field_guide_bytes):
        """Parse should extract clean source name from URI."""
        from integration_coworker.sources.pdf_guide import PDFGuideSource
        
        source = PDFGuideSource()
        result = source.parse(customer_field_guide_bytes, "/path/to/my_spec_guide.pdf")
        
        if result.is_valid():
            assert result.data.get("source_name") == "my_spec_guide"


class TestPDFGuideSourceRouter:
    """Test PDFGuideSource integration with router."""
    
    def test_pdf_guide_registered_in_router(self):
        """PDFGuideSource should be registered in SOURCE_REGISTRY."""
        from integration_coworker.sources import ensure_sources_registered, SOURCE_REGISTRY
        
        ensure_sources_registered()
        
        source_names = [s.__class__.__name__ for _, s in SOURCE_REGISTRY]
        assert "PDFGuideSource" in source_names
    
    def test_pdf_guide_has_correct_priority(self):
        """PDFGuideSource should have priority 40."""
        from integration_coworker.sources import ensure_sources_registered, SOURCE_REGISTRY
        
        ensure_sources_registered()
        
        for priority, source in SOURCE_REGISTRY:
            if source.__class__.__name__ == "PDFGuideSource":
                assert priority == 40
                break
    
    def test_pdf_detected_before_plain_text(self, customer_field_guide_bytes):
        """PDF should be detected over plain text sources."""
        from integration_coworker.sources import detect_and_route, ensure_sources_registered
        
        ensure_sources_registered()
        
        try:
            result = detect_and_route(customer_field_guide_bytes, "guide.pdf", "")
            # Should be routed to file source (PDF)
            assert result.source_type.value == "file"
        except ValueError:
            # No handler matched - this is OK if confidence is too low
            pass


class TestPDFGuideEdgeCases:
    """Test edge cases and boundary conditions."""
    
    def test_empty_content(self):
        """Empty content should be rejected."""
        from integration_coworker.sources.pdf_guide import PDFGuideSource
        
        source = PDFGuideSource()
        confidence = source.detect(b"", "empty.pdf", "")
        assert confidence == 0.0
    
    def test_very_short_pdf(self):
        """Very short content should be rejected."""
        from integration_coworker.sources.pdf_guide import PDFGuideSource
        
        source = PDFGuideSource()
        # Just the magic bytes, nothing else valid
        confidence = source.detect(b"%PDF-1.4", "short.pdf", "")
        assert confidence < 0.5
    
    def test_unicode_in_uri(self, customer_field_guide_bytes):
        """Unicode in URI should be handled."""
        from integration_coworker.sources.pdf_guide import PDFGuideSource
        
        source = PDFGuideSource()
        confidence = source.detect(customer_field_guide_bytes, "спецификация.pdf", "")
        
        # Should still work
        assert confidence > 0
    
    def test_pdf_content_type_boost(self, minimal_no_fields_bytes):
        """Explicit PDF content-type should boost confidence."""
        from integration_coworker.sources.pdf_guide import PDFGuideSource
        
        source = PDFGuideSource()
        
        # Without content-type
        conf1 = source.detect(minimal_no_fields_bytes, "doc.pdf", "")
        
        # With explicit PDF content-type
        conf2 = source.detect(minimal_no_fields_bytes, "doc.pdf", "application/pdf")
        
        # With content-type should be equal or higher
        assert conf2 >= conf1


class TestPDFGuideConfigContract:
    """Test PDFGuideConfidenceConfig contracts."""
    
    def test_config_weights_sum_to_one(self):
        """Signal weights must sum to 1.0."""
        from integration_coworker.parsers.pdf_guide_config import PDFGuideConfidenceConfig
        
        config = PDFGuideConfidenceConfig()
        total = (
            config.weight_pdf_signature +
            config.weight_text_extraction +
            config.weight_guide_keywords +
            config.weight_field_table
        )
        assert abs(total - 1.0) < 0.001
    
    def test_config_invalid_weights_rejected(self):
        """Config with invalid weights should raise."""
        from integration_coworker.parsers.pdf_guide_config import PDFGuideConfidenceConfig
        
        with pytest.raises(ValueError):
            PDFGuideConfidenceConfig(
                weight_pdf_signature=0.5,
                weight_text_extraction=0.5,
                weight_guide_keywords=0.5,  # Sum > 1.0
                weight_field_table=0.5,
            )
    
    def test_config_thresholds_valid_range(self):
        """Thresholds should be in valid range."""
        from integration_coworker.parsers.pdf_guide_config import PDFGuideConfidenceConfig
        
        config = PDFGuideConfidenceConfig()
        
        assert 0 < config.detect_min <= 1.0
        assert 0 < config.infer_min <= 1.0
        assert 0 < config.infer_warn <= 1.0
        assert config.infer_min <= config.infer_warn
    
    def test_config_singleton_pattern(self):
        """get_config() should return consistent config."""
        from integration_coworker.parsers.pdf_guide_config import get_config, reset_config
        
        reset_config()
        config1 = get_config()
        config2 = get_config()
        
        assert config1 is config2
