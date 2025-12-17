"""
HTML specification parser for extracting API documentation from HTML pages.

Implements: V1 Gap Closure Plan P4 - HTML Spec Parsing
Uses BeautifulSoup for DOM parsing and text heuristics for endpoint detection.
"""
from dataclasses import dataclass, field
from typing import Optional, Any

try:
    from bs4 import BeautifulSoup, Tag
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False
    BeautifulSoup = None
    Tag = None

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
class HtmlSpecDocument:
    """
    Represents a parsed HTML API specification document.
    
    Contains extracted endpoints, schemas, and events along with
    metadata from the HTML structure.
    """
    title: Optional[str] = None
    description: Optional[str] = None
    base_url: Optional[str] = None
    endpoints: list[ParsedEndpoint] = field(default_factory=list)
    schemas: list[ParsedSchema] = field(default_factory=list)
    events: list[ParsedEvent] = field(default_factory=list)
    raw_text: Optional[str] = None
    sections: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def parse_html_spec(html_content: str) -> HtmlSpecDocument:
    """
    Parse an HTML document to extract API specification information.
    
    Uses BeautifulSoup for DOM parsing and regex heuristics for
    endpoint detection from the extracted text.
    
    Args:
        html_content: Raw HTML content string
        
    Returns:
        HtmlSpecDocument containing extracted API information
    """
    doc = HtmlSpecDocument()

    if not HAS_BS4:
        doc.errors.append(
            "beautifulsoup4 not installed. Install with: pip install beautifulsoup4"
        )
        return doc

    try:
        soup = BeautifulSoup(html_content, 'html.parser')
    except Exception as e:
        doc.errors.append(f"Failed to parse HTML: {str(e)}")
        return doc

    # Extract title
    title_tag = soup.find('title')
    if title_tag:
        doc.title = title_tag.get_text(strip=True)

    # Try to find description from meta tags
    meta_desc = soup.find('meta', attrs={'name': 'description'})
    if meta_desc and meta_desc.get('content'):
        doc.description = meta_desc['content']

    # Try to find base URL from various sources
    doc.base_url = _extract_base_url(soup)

    # Extract text content, preserving some structure
    raw_text = _extract_text_content(soup)
    doc.raw_text = raw_text

    # Extract sections (headers and their content)
    doc.sections = _extract_sections(soup)

    # Detect endpoints from text
    doc.endpoints = detect_endpoints(raw_text)

    # Also extract endpoints from structured HTML elements
    structured_endpoints = extract_html_endpoints(soup)

    # Merge, avoiding duplicates
    seen = {(ep.method, ep.path) for ep in doc.endpoints}
    for ep in structured_endpoints:
        if (ep.method, ep.path) not in seen:
            doc.endpoints.append(ep)
            seen.add((ep.method, ep.path))

    # Infer schemas for each endpoint
    for endpoint in doc.endpoints:
        req_schema = infer_request_schema(raw_text, endpoint)
        if req_schema:
            doc.schemas.append(req_schema)

        resp_schema = infer_response_schema(raw_text, endpoint)
        if resp_schema:
            doc.schemas.append(resp_schema)

    # Detect events/webhooks
    doc.events = detect_events(raw_text)

    return doc


def extract_html_endpoints(soup_or_html: Any) -> list[ParsedEndpoint]:
    """
    Extract API endpoints from HTML DOM structure.
    
    Looks for common patterns used in API documentation:
    - Code blocks containing HTTP method + path
    - Tables with method/path columns
    - Definition lists
    - Elements with specific classes (like 'endpoint', 'api-method')
    
    Args:
        soup_or_html: BeautifulSoup object or HTML string
        
    Returns:
        List of ParsedEndpoint objects
    """
    if not HAS_BS4:
        return []

    if isinstance(soup_or_html, str):
        soup = BeautifulSoup(soup_or_html, 'html.parser')
    else:
        soup = soup_or_html

    endpoints: list[ParsedEndpoint] = []
    seen: set[tuple[str, str]] = set()

    # Pattern 1: Look for code blocks with endpoint definitions
    for code in soup.find_all(['code', 'pre']):
        text = code.get_text()
        detected = detect_endpoints(text)
        for ep in detected:
            key = (ep.method, ep.path)
            if key not in seen:
                seen.add(key)
                endpoints.append(ep)

    # Pattern 2: Look for elements with endpoint-related classes
    import re
    endpoint_class_patterns = [
        re.compile(r'endpoint', re.IGNORECASE),
        re.compile(r'api-method', re.IGNORECASE),
        re.compile(r'http-method', re.IGNORECASE),
        re.compile(r'route', re.IGNORECASE),
    ]
    for pattern in endpoint_class_patterns:
        for elem in soup.find_all(class_=pattern):
            text = elem.get_text()
            detected = detect_endpoints(text)
            for ep in detected:
                key = (ep.method, ep.path)
                if key not in seen:
                    seen.add(key)
                    endpoints.append(ep)

    # Pattern 3: Look for tables that might contain endpoint info
    for table in soup.find_all('table'):
        table_endpoints = _extract_endpoints_from_table(table)
        for ep in table_endpoints:
            key = (ep.method, ep.path)
            if key not in seen:
                seen.add(key)
                endpoints.append(ep)

    # Pattern 4: Look for definition lists
    for dl in soup.find_all('dl'):
        dl_text = dl.get_text()
        detected = detect_endpoints(dl_text)
        for ep in detected:
            key = (ep.method, ep.path)
            if key not in seen:
                seen.add(key)
                endpoints.append(ep)

    return endpoints


def _extract_base_url(soup: Any) -> Optional[str]:
    """Extract base URL from HTML document."""
    # Check for base tag
    base_tag = soup.find('base')
    if base_tag and base_tag.get('href'):
        return base_tag['href']

    # Check for server URL in text
    import re
    text = soup.get_text()

    # Look for common base URL patterns
    patterns = [
        r'base\s*url[:\s]+(["\']?)(https?://[^\s"\'<>]+)\1',
        r'api\s*(?:url|endpoint)[:\s]+(["\']?)(https?://[^\s"\'<>]+)\1',
        r'server[:\s]+(["\']?)(https?://[^\s"\'<>]+)\1',
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(2)

    return None


def _extract_text_content(soup: Any) -> str:
    """
    Extract text content from HTML, removing scripts and styles.
    Preserves some structure with newlines.
    """
    # Remove script and style elements
    for script in soup(['script', 'style', 'nav', 'footer', 'header']):
        script.decompose()

    # Get text with newlines for block elements
    text_parts = []

    for element in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'div', 'li', 'code', 'pre', 'td', 'th']):
        text = element.get_text(strip=True)
        if text:
            text_parts.append(text)

    return '\n'.join(text_parts)


def _extract_sections(soup: Any) -> list[dict[str, Any]]:
    """Extract sections based on header elements."""
    sections = []

    for header in soup.find_all(['h1', 'h2', 'h3', 'h4']):
        section = {
            'level': int(header.name[1]),
            'title': header.get_text(strip=True),
            'content': '',
        }

        # Get content until next header of same or higher level
        content_parts = []
        for sibling in header.find_next_siblings():
            if sibling.name and sibling.name in ['h1', 'h2', 'h3', 'h4']:
                sibling_level = int(sibling.name[1])
                if sibling_level <= section['level']:
                    break
            content_parts.append(sibling.get_text(strip=True))

        section['content'] = '\n'.join(content_parts)
        sections.append(section)

    return sections


def _extract_endpoints_from_table(table: Any) -> list[ParsedEndpoint]:
    """Extract endpoints from an HTML table."""
    endpoints = []

    # Find header row to identify columns
    headers = []
    header_row = table.find('tr')
    if header_row:
        for th in header_row.find_all(['th', 'td']):
            headers.append(th.get_text(strip=True).lower())

    # Look for method and path columns
    method_col = None
    path_col = None
    desc_col = None

    for i, header in enumerate(headers):
        if 'method' in header or header in ['http', 'verb']:
            method_col = i
        elif 'path' in header or 'endpoint' in header or 'url' in header or 'route' in header:
            path_col = i
        elif 'description' in header or 'desc' in header:
            desc_col = i

    if method_col is None or path_col is None:
        return endpoints

    # Parse data rows
    for row in table.find_all('tr')[1:]:  # Skip header row
        cells = row.find_all(['td', 'th'])
        if len(cells) > max(method_col, path_col):
            method = cells[method_col].get_text(strip=True).upper()
            path = cells[path_col].get_text(strip=True)

            if method in ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS']:
                desc = None
                if desc_col is not None and len(cells) > desc_col:
                    desc = cells[desc_col].get_text(strip=True)

                endpoints.append(ParsedEndpoint(
                    method=method,
                    path=path,
                    description=desc,
                ))

    return endpoints
