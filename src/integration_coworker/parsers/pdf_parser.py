"""
PDF specification parser for extracting API documentation from PDF files.

Implements: V1 Gap Closure Plan P4 - PDF Spec Parsing
Uses pypdf for text extraction and text heuristics for endpoint detection.
"""
from dataclasses import dataclass, field
from typing import Optional, Any, BinaryIO, Union
from pathlib import Path

try:
    from pypdf import PdfReader
    HAS_PYPDF = True
except ImportError:
    try:
        # Fallback to older PyPDF2 if pypdf not available
        from PyPDF2 import PdfReader
        HAS_PYPDF = True
    except ImportError:
        HAS_PYPDF = False
        PdfReader = None

from integration_coworker.parsers.text_spec_heuristics import (
    detect_endpoints,
    detect_events,
    infer_request_schema,
    infer_response_schema,
    ParsedEndpoint,
    ParsedSchema,
    ParsedEvent,
)


@dataclass
class PdfSpecDocument:
    """
    Represents a parsed PDF API specification document.
    
    Contains extracted endpoints, schemas, and events along with
    metadata from the PDF structure.
    """
    title: Optional[str] = None
    author: Optional[str] = None
    num_pages: int = 0
    endpoints: list[ParsedEndpoint] = field(default_factory=list)
    schemas: list[ParsedSchema] = field(default_factory=list)
    events: list[ParsedEvent] = field(default_factory=list)
    raw_text: Optional[str] = None
    page_texts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def extract_pdf_text(
    pdf_source: Union[str, Path, BinaryIO, bytes],
    max_pages: Optional[int] = None
) -> tuple[str, list[str], dict[str, Any]]:
    """
    Extract text content from a PDF file.
    
    Args:
        pdf_source: File path, Path object, file-like object, or bytes
        max_pages: Maximum number of pages to extract (None for all)
        
    Returns:
        Tuple of (full_text, list_of_page_texts, metadata_dict)
    """
    if not HAS_PYPDF:
        raise ImportError(
            "pypdf not installed. Install with: pip install pypdf"
        )

    # Handle different input types
    if isinstance(pdf_source, bytes):
        import io
        pdf_source = io.BytesIO(pdf_source)
    elif isinstance(pdf_source, (str, Path)):
        pdf_source = open(pdf_source, 'rb')

    try:
        reader = PdfReader(pdf_source)

        # Extract metadata
        metadata: dict[str, Any] = {}
        if reader.metadata:
            metadata = {
                'title': reader.metadata.get('/Title'),
                'author': reader.metadata.get('/Author'),
                'subject': reader.metadata.get('/Subject'),
                'creator': reader.metadata.get('/Creator'),
            }
        metadata['num_pages'] = len(reader.pages)

        # Extract text from each page
        page_texts: list[str] = []
        pages_to_read = len(reader.pages)
        if max_pages is not None:
            pages_to_read = min(max_pages, pages_to_read)

        for i in range(pages_to_read):
            page = reader.pages[i]
            text = page.extract_text() or ""
            page_texts.append(text)

        full_text = '\n\n'.join(page_texts)

        return full_text, page_texts, metadata

    finally:
        # Close file if we opened it
        if isinstance(pdf_source, (str, Path)):
            pass  # Already closed by context manager
        elif hasattr(pdf_source, 'close') and hasattr(pdf_source, 'name'):
            pdf_source.close()


def parse_pdf_spec(
    pdf_source: Union[str, Path, BinaryIO, bytes],
    max_pages: Optional[int] = None
) -> PdfSpecDocument:
    """
    Parse a PDF document to extract API specification information.
    
    Uses pypdf for text extraction and regex heuristics for
    endpoint detection from the extracted text.
    
    Args:
        pdf_source: File path, Path object, file-like object, or bytes
        max_pages: Maximum number of pages to extract
        
    Returns:
        PdfSpecDocument containing extracted API information
    """
    doc = PdfSpecDocument()

    if not HAS_PYPDF:
        doc.errors.append(
            "pypdf not installed. Install with: pip install pypdf"
        )
        return doc

    try:
        full_text, page_texts, metadata = extract_pdf_text(pdf_source, max_pages)

        doc.raw_text = full_text
        doc.page_texts = page_texts
        doc.title = metadata.get('title')
        doc.author = metadata.get('author')
        doc.num_pages = metadata.get('num_pages', 0)

    except Exception as e:
        doc.errors.append(f"Failed to extract PDF text: {str(e)}")
        return doc

    # Detect endpoints from text
    doc.endpoints = detect_endpoints(full_text)

    # Infer schemas for each endpoint
    for endpoint in doc.endpoints:
        req_schema = infer_request_schema(full_text, endpoint)
        if req_schema:
            doc.schemas.append(req_schema)

        resp_schema = infer_response_schema(full_text, endpoint)
        if resp_schema:
            doc.schemas.append(resp_schema)

    # Detect events/webhooks
    doc.events = detect_events(full_text)

    return doc


def parse_pdf_bytes(pdf_bytes: bytes, max_pages: Optional[int] = None) -> PdfSpecDocument:
    """
    Parse PDF from raw bytes.
    
    Convenience function for when PDF content is already loaded in memory.
    
    Args:
        pdf_bytes: Raw PDF file bytes
        max_pages: Maximum number of pages to extract
        
    Returns:
        PdfSpecDocument containing extracted API information
    """
    return parse_pdf_spec(pdf_bytes, max_pages)
