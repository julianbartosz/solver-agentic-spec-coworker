"""
Text-based heuristics for extracting API specifications from unstructured text.

Implements: V1 Gap Closure Plan P4 - Text Spec Heuristics
Uses regex patterns to detect endpoints, schemas, and events from raw text
extracted from HTML or PDF documents.
"""
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParsedEndpoint:
    """
    An endpoint detected from unstructured text via heuristics.
    
    Represents the intermediate parsed state before conversion to Silver model.
    """
    method: str  # GET, POST, PUT, DELETE, PATCH
    path: str  # /v1/customers/{id}
    summary: Optional[str] = None
    description: Optional[str] = None
    request_content_type: Optional[str] = None
    response_content_type: Optional[str] = None
    auth_hint: Optional[str] = None  # e.g., "Bearer", "API Key"
    source_line: Optional[int] = None
    source_context: Optional[str] = None  # Surrounding text for debugging


@dataclass
class ParsedSchema:
    """
    A schema inferred from text descriptions or code examples.
    """
    name: str
    fields: dict[str, dict] = field(default_factory=dict)
    # fields maps field_name -> {"type": "string", "required": True, ...}
    description: Optional[str] = None
    source_context: Optional[str] = None


@dataclass
class ParsedEvent:
    """
    A webhook event detected from text.
    """
    name: str
    description: Optional[str] = None
    payload_fields: dict[str, dict] = field(default_factory=dict)
    source_context: Optional[str] = None


# Regex pattern for detecting HTTP endpoints
# Matches: GET /v1/customers/{id} or POST /api/users
# Uses \b for word boundary to handle leading whitespace
ENDPOINT_PATTERN = re.compile(
    r"\b(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/[\w/\-{}.:]+)",
    re.IGNORECASE
)

# Alternative pattern for endpoints written as "GET: /path" or "Method: GET, Path: /path"
ENDPOINT_ALT_PATTERN = re.compile(
    r"(?:method|http\s*method)[\s:]+\s*(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)"
    r"[\s,]*(?:path|endpoint|url|uri)[\s:]+\s*(/[\w/\-{}.:]+)",
    re.IGNORECASE
)

# Pattern for URLs that look like API endpoints
URL_ENDPOINT_PATTERN = re.compile(
    r"https?://[^/\s]+(/(?:api|v\d+)/[\w/\-{}.:]+)",
    re.IGNORECASE
)

# Pattern for detecting authentication hints
AUTH_PATTERNS = {
    "bearer": re.compile(r"bearer\s+(?:token|auth)", re.IGNORECASE),
    "api_key": re.compile(r"api[\s\-_]?key", re.IGNORECASE),
    "basic": re.compile(r"basic\s+auth", re.IGNORECASE),
    "oauth": re.compile(r"oauth\s*2?\.?0?", re.IGNORECASE),
}

# Pattern for detecting JSON field definitions in text
FIELD_PATTERN = re.compile(
    r'["\']?([\w_]+)["\']?\s*[:\-]\s*(?:\(?(string|integer|number|boolean|array|object|date|datetime|uuid|email)\)?)?',
    re.IGNORECASE
)

# Pattern for detecting webhook/event names
# Matches patterns like "event: customer.created" or "webhook event: payment.completed"
# The event name must contain a dot (like customer.created) to distinguish from false positives
EVENT_PATTERN = re.compile(
    r"(?:event|webhook|notification)[\s:]+[\"']?([\w]+\.[\w._-]+)[\"']?",
    re.IGNORECASE
)


def detect_endpoints(text: str) -> list[ParsedEndpoint]:
    """
    Detect API endpoints from unstructured text using regex heuristics.
    
    Args:
        text: Raw text from HTML or PDF document
        
    Returns:
        List of ParsedEndpoint objects detected from the text
    """
    endpoints: list[ParsedEndpoint] = []
    lines = text.split('\n')

    # Track seen endpoints to avoid duplicates
    seen: set[tuple[str, str]] = set()

    # Primary pattern: "GET /path"
    for match in ENDPOINT_PATTERN.finditer(text):
        method = match.group(1).upper()
        path = match.group(2)
        key = (method, path)

        if key not in seen:
            seen.add(key)
            # Find line number
            line_num = text[:match.start()].count('\n') + 1
            # Get context (surrounding lines)
            context = _get_context(lines, line_num)
            # Detect auth hint from context
            auth_hint = _detect_auth_hint(context)

            endpoints.append(ParsedEndpoint(
                method=method,
                path=path,
                source_line=line_num,
                source_context=context,
                auth_hint=auth_hint,
            ))

    # Alternative pattern: "Method: GET, Path: /api/foo"
    for match in ENDPOINT_ALT_PATTERN.finditer(text):
        method = match.group(1).upper()
        path = match.group(2)
        key = (method, path)

        if key not in seen:
            seen.add(key)
            line_num = text[:match.start()].count('\n') + 1
            context = _get_context(lines, line_num)
            auth_hint = _detect_auth_hint(context)

            endpoints.append(ParsedEndpoint(
                method=method,
                path=path,
                source_line=line_num,
                source_context=context,
                auth_hint=auth_hint,
            ))

    # URL pattern: Extract path from full URLs
    for match in URL_ENDPOINT_PATTERN.finditer(text):
        path = match.group(1)
        # Try to find method from nearby text
        line_num = text[:match.start()].count('\n') + 1
        context = _get_context(lines, line_num)
        method = _infer_method_from_context(context)
        key = (method, path)

        if key not in seen:
            seen.add(key)
            auth_hint = _detect_auth_hint(context)

            endpoints.append(ParsedEndpoint(
                method=method,
                path=path,
                source_line=line_num,
                source_context=context,
                auth_hint=auth_hint,
            ))

    return endpoints


def infer_request_schema(
    text: str,
    endpoint: Optional[ParsedEndpoint] = None
) -> Optional[ParsedSchema]:
    """
    Infer request schema from text, optionally using endpoint context.
    
    Looks for JSON-like field definitions or documented parameters.
    
    Args:
        text: Text to analyze
        endpoint: Optional endpoint to help narrow context
        
    Returns:
        ParsedSchema if fields were detected, None otherwise
    """
    # If we have endpoint context, use it; otherwise use full text
    if endpoint and endpoint.source_context:
        context_text = endpoint.source_context
    else:
        context_text = text

    # Look for "request body" or "parameters" sections
    request_section = _extract_section(context_text, [
        "request body", "request", "parameters", "input", "payload"
    ])

    fields = _extract_fields(request_section or context_text)

    if not fields:
        return None

    schema_name = "RequestBody"
    if endpoint:
        # Generate a name based on endpoint
        path_parts = endpoint.path.strip('/').split('/')
        if path_parts:
            schema_name = f"{path_parts[-1].title().replace('{', '').replace('}', '')}Request"

    return ParsedSchema(
        name=schema_name,
        fields=fields,
        source_context=request_section,
    )


def infer_response_schema(
    text: str,
    endpoint: Optional[ParsedEndpoint] = None
) -> Optional[ParsedSchema]:
    """
    Infer response schema from text, optionally using endpoint context.
    
    Args:
        text: Text to analyze
        endpoint: Optional endpoint to help narrow context
        
    Returns:
        ParsedSchema if fields were detected, None otherwise
    """
    if endpoint and endpoint.source_context:
        context_text = endpoint.source_context
    else:
        context_text = text

    # Look for "response" sections
    response_section = _extract_section(context_text, [
        "response body", "response", "returns", "output", "result"
    ])

    fields = _extract_fields(response_section or context_text)

    if not fields:
        return None

    schema_name = "ResponseBody"
    if endpoint:
        path_parts = endpoint.path.strip('/').split('/')
        if path_parts:
            schema_name = f"{path_parts[-1].title().replace('{', '').replace('}', '')}Response"

    return ParsedSchema(
        name=schema_name,
        fields=fields,
        source_context=response_section,
    )


def detect_events(text: str) -> list[ParsedEvent]:
    """
    Detect webhook/event definitions from text.
    
    Args:
        text: Text to analyze
        
    Returns:
        List of ParsedEvent objects
    """
    events: list[ParsedEvent] = []
    seen: set[str] = set()
    lines = text.split('\n')

    for match in EVENT_PATTERN.finditer(text):
        event_name = match.group(1)

        if event_name not in seen:
            seen.add(event_name)
            line_num = text[:match.start()].count('\n') + 1
            context = _get_context(lines, line_num)

            # Try to extract payload fields from context
            payload_fields = _extract_fields(context)

            events.append(ParsedEvent(
                name=event_name,
                payload_fields=payload_fields,
                source_context=context,
            ))

    return events


def _get_context(lines: list[str], line_num: int, window: int = 5) -> str:
    """Get context lines around a specific line number."""
    start = max(0, line_num - window - 1)
    end = min(len(lines), line_num + window)
    return '\n'.join(lines[start:end])


def _detect_auth_hint(context: str) -> Optional[str]:
    """Detect authentication type hint from context."""
    for auth_type, pattern in AUTH_PATTERNS.items():
        if pattern.search(context):
            return auth_type
    return None


def _infer_method_from_context(context: str) -> str:
    """Try to infer HTTP method from context text."""
    context_lower = context.lower()

    if any(word in context_lower for word in ["create", "post", "add", "insert"]):
        return "POST"
    elif any(word in context_lower for word in ["update", "put", "modify", "edit"]):
        return "PUT"
    elif any(word in context_lower for word in ["patch", "partial"]):
        return "PATCH"
    elif any(word in context_lower for word in ["delete", "remove", "destroy"]):
        return "DELETE"
    else:
        return "GET"  # Default


def _extract_section(text: str, section_names: list[str]) -> Optional[str]:
    """Extract a section of text based on header patterns."""
    text_lower = text.lower()

    for name in section_names:
        # Look for section header
        pattern = re.compile(
            rf"(?:^|\n)\s*#+?\s*{re.escape(name)}[:\s]*\n(.*?)(?=\n\s*#+?\s*\w|$)",
            re.IGNORECASE | re.DOTALL
        )
        match = pattern.search(text)
        if match:
            return match.group(1).strip()

    return None


def _extract_fields(text: str) -> dict[str, dict]:
    """Extract field definitions from text."""
    fields: dict[str, dict] = {}

    for match in FIELD_PATTERN.finditer(text):
        field_name = match.group(1)
        field_type = match.group(2) if match.group(2) else "string"

        # Skip common non-field words
        skip_words = {"the", "a", "an", "is", "are", "for", "to", "of", "in", "on"}
        if field_name.lower() in skip_words:
            continue

        # Check if required (look for "required" nearby)
        start = max(0, match.start() - 50)
        end = min(len(text), match.end() + 50)
        nearby = text[start:end].lower()
        required = "required" in nearby

        fields[field_name] = {
            "type": field_type.lower(),
            "required": required,
        }

    return fields
