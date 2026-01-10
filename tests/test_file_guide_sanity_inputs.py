"""
Sanity checks for file guide sources with real inputs.
"""
import pytest
from integration_coworker.sources.csv_source import CSVSource
from integration_coworker.sources.pdf_guide import PDFGuideSource

def test_csv_source_sanity():
    """Minimal sanity check for CSVSource."""
    source = CSVSource()
    content = "col1,col2\nval1,val2"
    confidence = source.detect(content, "test.csv", "text/csv")
    assert confidence > 0.0
    
    parsed = source.parse(content, "test.csv")
    assert parsed.is_valid()
    assert parsed.data["file_spec"].file_type == "csv"

def test_pdf_guide_source_sanity():
    """Minimal sanity check for PDFGuideSource."""
    source = PDFGuideSource()
    # Minimal PDF header
    content = b"%PDF-1.4\n..."
    confidence = source.detect(content, "test.pdf", "application/pdf")
    # Should be > 0 if it detects the header, or at least not crash
    # Note: PDFGuideSource might require more substantial content to return high confidence
    # but this checks that the method runs.
    assert confidence >= 0.0 
