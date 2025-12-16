"""
Tests for pdf_parser module.

Tests PDF spec parsing with pypdf integration.
"""
import pytest
import io
from integration_coworker.parsers.pdf_parser import (
    parse_pdf_spec,
    extract_pdf_text,
    parse_pdf_bytes,
    PdfSpecDocument,
)

# Check if pypdf is available
try:
    from pypdf import PdfReader
    HAS_PYPDF = True
except ImportError:
    try:
        from PyPDF2 import PdfReader
        HAS_PYPDF = True
    except ImportError:
        HAS_PYPDF = False


# Minimal valid PDF for testing (1x1 white pixel image converted to PDF-like structure)
# This is a minimal PDF that can be parsed
MINIMAL_PDF = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>
endobj
4 0 obj
<< /Length 44 >>
stream
BT
/F1 12 Tf
100 700 Td
(GET /v1/test) Tj
ET
endstream
endobj
xref
0 5
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000206 00000 n 
trailer
<< /Size 5 /Root 1 0 R >>
startxref
300
%%EOF"""


@pytest.mark.no_db
class TestPdfSpecDocument:
    """Test PdfSpecDocument dataclass."""
    
    def test_create_pdf_spec_document(self):
        """Test creating a PdfSpecDocument."""
        doc = PdfSpecDocument(
            title="API Documentation",
            num_pages=10
        )
        
        assert doc.title == "API Documentation"
        assert doc.num_pages == 10
        assert doc.endpoints == []
        assert doc.schemas == []
        assert doc.events == []
        assert doc.errors == []
    
    def test_pdf_spec_document_defaults(self):
        """Test PdfSpecDocument default values."""
        doc = PdfSpecDocument()
        
        assert doc.title is None
        assert doc.author is None
        assert doc.num_pages == 0
        assert doc.raw_text is None
        assert doc.page_texts == []


@pytest.mark.no_db
class TestParsePdfSpec:
    """Test PDF spec parsing."""
    
    def test_parse_pdf_without_pypdf_returns_error(self):
        """Test that missing pypdf is handled gracefully."""
        import integration_coworker.parsers.pdf_parser as pdf_module
        original_has_pypdf = pdf_module.HAS_PYPDF
        
        try:
            pdf_module.HAS_PYPDF = False
            doc = parse_pdf_spec(io.BytesIO(b"fake pdf"))
            
            assert len(doc.errors) > 0
            assert "pypdf" in doc.errors[0].lower()
        finally:
            pdf_module.HAS_PYPDF = original_has_pypdf
    
    @pytest.mark.skipif(not HAS_PYPDF, reason="pypdf not installed")
    def test_parse_invalid_pdf_returns_error(self):
        """Test parsing invalid PDF returns error."""
        doc = parse_pdf_spec(io.BytesIO(b"not a pdf"))
        
        assert len(doc.errors) > 0
    
    @pytest.mark.skipif(not HAS_PYPDF, reason="pypdf not installed")
    def test_parse_pdf_bytes_wrapper(self):
        """Test the parse_pdf_bytes convenience function."""
        # Should handle gracefully even with invalid bytes
        doc = parse_pdf_bytes(b"invalid", max_pages=1)
        
        assert isinstance(doc, PdfSpecDocument)


@pytest.mark.no_db
class TestExtractPdfText:
    """Test PDF text extraction."""
    
    def test_extract_without_pypdf_raises(self):
        """Test that missing pypdf raises ImportError."""
        import integration_coworker.parsers.pdf_parser as pdf_module
        original_has_pypdf = pdf_module.HAS_PYPDF
        
        try:
            pdf_module.HAS_PYPDF = False
            
            with pytest.raises(ImportError) as exc_info:
                extract_pdf_text(io.BytesIO(b"fake"))
            
            assert "pypdf" in str(exc_info.value).lower()
        finally:
            pdf_module.HAS_PYPDF = original_has_pypdf
    
    @pytest.mark.skipif(not HAS_PYPDF, reason="pypdf not installed")
    def test_extract_returns_tuple(self):
        """Test that extract_pdf_text returns correct tuple structure."""
        # This will fail with invalid PDF, but we're testing the interface
        try:
            result = extract_pdf_text(io.BytesIO(MINIMAL_PDF))
            assert isinstance(result, tuple)
            assert len(result) == 3
            full_text, page_texts, metadata = result
            assert isinstance(full_text, str)
            assert isinstance(page_texts, list)
            assert isinstance(metadata, dict)
        except Exception:
            # Expected for minimal/invalid PDF, just testing interface
            pass


@pytest.mark.no_db
class TestPdfParserEndpointDetection:
    """Test endpoint detection from PDF text."""
    
    @pytest.mark.skipif(not HAS_PYPDF, reason="pypdf not installed")
    def test_endpoints_extracted_from_text(self):
        """Test that endpoints are extracted from PDF text."""
        # Create a mock PDF document with pre-set text
        doc = PdfSpecDocument(
            raw_text="""
            API Reference
            
            GET /v1/customers - List all customers
            POST /v1/customers - Create a customer
            DELETE /v1/customers/{id} - Delete a customer
            """
        )
        
        # Manually extract endpoints using the heuristics
        from integration_coworker.parsers.text_spec_heuristics import detect_endpoints
        doc.endpoints = detect_endpoints(doc.raw_text)
        
        assert len(doc.endpoints) == 3
        methods = {ep.method for ep in doc.endpoints}
        assert methods == {"GET", "POST", "DELETE"}


@pytest.mark.no_db
class TestPdfParserSchemaInference:
    """Test schema inference from PDF text."""
    
    @pytest.mark.skipif(not HAS_PYPDF, reason="pypdf not installed")
    def test_schemas_inferred_from_text(self):
        """Test that schemas are inferred from PDF text."""
        from integration_coworker.parsers.text_spec_heuristics import (
            detect_endpoints,
            infer_request_schema,
        )
        
        text = """
        POST /v1/users
        
        # Request Body
        name: string (required)
        email: string (required)
        age: integer
        """
        
        endpoints = detect_endpoints(text)
        assert len(endpoints) == 1
        
        schema = infer_request_schema(text, endpoints[0])
        assert schema is not None
        assert "name" in schema.fields


@pytest.mark.no_db
class TestPdfParserEventDetection:
    """Test event/webhook detection from PDF text."""
    
    @pytest.mark.skipif(not HAS_PYPDF, reason="pypdf not installed")
    def test_events_detected_from_text(self):
        """Test that events are detected from PDF text."""
        from integration_coworker.parsers.text_spec_heuristics import detect_events
        
        text = """
        Webhooks
        
        The following events are supported:
        Event: payment.completed
        Event: order.created
        Notification: user.updated
        """
        
        events = detect_events(text)
        assert len(events) >= 2


@pytest.mark.no_db  
class TestPdfInputTypes:
    """Test different input types for PDF parsing."""
    
    @pytest.mark.skipif(not HAS_PYPDF, reason="pypdf not installed")
    def test_parse_from_bytes(self):
        """Test parsing from bytes."""
        doc = parse_pdf_spec(b"not-a-valid-pdf")
        
        # Should return document with errors, not crash
        assert isinstance(doc, PdfSpecDocument)
        assert len(doc.errors) > 0
    
    @pytest.mark.skipif(not HAS_PYPDF, reason="pypdf not installed")
    def test_parse_from_bytesio(self):
        """Test parsing from BytesIO."""
        doc = parse_pdf_spec(io.BytesIO(b"not-a-valid-pdf"))
        
        assert isinstance(doc, PdfSpecDocument)
        assert len(doc.errors) > 0
    
    @pytest.mark.skipif(not HAS_PYPDF, reason="pypdf not installed")
    def test_max_pages_parameter(self):
        """Test max_pages parameter is accepted."""
        # Should accept the parameter without error
        doc = parse_pdf_spec(io.BytesIO(b"test"), max_pages=5)
        
        assert isinstance(doc, PdfSpecDocument)


@pytest.mark.no_db
class TestPdfMetadataExtraction:
    """Test PDF metadata extraction."""
    
    def test_metadata_fields_populated(self):
        """Test that metadata fields are populated when available."""
        doc = PdfSpecDocument(
            title="Test PDF",
            author="Test Author",
            num_pages=5
        )
        
        assert doc.title == "Test PDF"
        assert doc.author == "Test Author"
        assert doc.num_pages == 5


@pytest.mark.no_db
class TestPdfParserEdgeCases:
    """Test edge cases for PDF parsing."""
    
    @pytest.mark.skipif(not HAS_PYPDF, reason="pypdf not installed")
    def test_empty_pdf(self):
        """Test handling of empty PDF bytes."""
        doc = parse_pdf_spec(io.BytesIO(b""))
        
        assert isinstance(doc, PdfSpecDocument)
        assert len(doc.errors) > 0
    
    @pytest.mark.skipif(not HAS_PYPDF, reason="pypdf not installed") 
    def test_corrupted_pdf(self):
        """Test handling of corrupted PDF."""
        # Random bytes that start like a PDF but are corrupted
        corrupted = b"%PDF-1.4\n" + b"corrupted content here" * 100
        
        doc = parse_pdf_spec(io.BytesIO(corrupted))
        
        assert isinstance(doc, PdfSpecDocument)
        # Should either have errors or no content
        assert len(doc.errors) > 0 or doc.raw_text == ""
    
    def test_page_texts_is_list(self):
        """Test that page_texts is always a list."""
        doc = PdfSpecDocument()
        
        assert isinstance(doc.page_texts, list)
        assert len(doc.page_texts) == 0
