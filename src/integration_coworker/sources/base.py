"""
Base classes and protocols for SpecSource plugins.

This module defines the core abstractions for the unified spec source system:
- SpecSource: Protocol for all source handlers
- StreamingSpecSource: Extended protocol for streaming-capable handlers
- ParsedSpec: Result container for parsed content
- SourceType: Enum for categorizing sources (API vs FILE)

All spec source implementations (OpenAPI, CSV, Fixed-Width, etc.) should
implement the SpecSource protocol. Sources that support true streaming
should also implement StreamingSpecSource.

Streaming Architecture (v2.1):
    - detect(): Fast content type detection (may sample)
    - parse(): Full parse (legacy, loads into memory)
    - detect_from_handle(): Streaming-aware detection
    - parse_from_handle(): TRUE streaming parse (no full-memory load)
    
    Sources implementing streaming should override parse_from_handle()
    to read directly from the handle's stream rather than loading bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Optional,
    Protocol,
    Union,
    runtime_checkable,
)

if TYPE_CHECKING:
    from .content_handle import ContentHandle


class SourceType(str, Enum):
    """Type of spec source."""
    
    API = "api"
    """API specification (OpenAPI, Swagger, GraphQL, etc.)"""
    
    FILE = "file"
    """File specification (CSV, Fixed-Width, Excel, EDI, etc.)"""
    
    MESSAGE = "message"
    """Message/event specification (Avro, Protobuf, JSON Schema, etc.)"""
    
    UNKNOWN = "unknown"
    """Unknown or unsupported source type"""


@dataclass
class ParsedSpec:
    """
    Result of parsing a spec source.
    
    This is the unified output format for all source handlers.
    The actual data structure in `data` depends on source_type:
    
    - API: OpenAPI/Swagger dict (compatible with existing pipeline)
    - FILE: {"file_spec": FileSpec, "fields": List[FileField], ...}
    - MESSAGE: {"message_spec": MessageSpec, ...}
    
    Attributes:
        source_type: Classification of the source (API, FILE, etc.)
        source_uri: Original URI/path of the source
        data: Parsed data structure (type depends on source_type)
        metadata: Additional metadata about the parsing
        errors: List of non-fatal errors/warnings
        confidence: Overall confidence score (0.0 to 1.0)
        raw_content: Original raw content (optional, for debugging)
    """
    
    source_type: SourceType
    source_uri: str
    data: Any  # OpenAPI dict for API, FileSpec bundle for FILE, etc.
    metadata: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    confidence: float = 1.0
    raw_content: Optional[bytes] = None
    
    def is_valid(self) -> bool:
        """Check if parsing succeeded (data is not None and no critical errors)."""
        return self.data is not None and not self.errors
    
    def add_error(self, error: str) -> None:
        """Add a parsing error."""
        self.errors.append(error)
    
    def add_warning(self, warning: str) -> None:
        """Add a parsing warning."""
        self.warnings.append(warning)
    
    def add_metadata(self, key: str, value: Any) -> None:
        """Add metadata entry."""
        self.metadata[key] = value


class SpecSource(Protocol):
    """
    Protocol for spec source handlers.
    
    All spec source implementations must implement these methods:
    
    - detect(): Score how well this handler matches content (0-1)
    - parse(): Parse content into a ParsedSpec
    
    Example implementation:
    
        class CSVSource:
            def detect(self, content, uri, content_type) -> float:
                if ".csv" in uri.lower():
                    return 0.95
                return 0.0
            
            def parse(self, content, uri) -> ParsedSpec:
                # Parse CSV and return FileSpec
                ...
    
    Registration:
    
        from integration_coworker.sources import register_source
        register_source(CSVSource(), priority=70)
    """
    
    def detect(
        self,
        content: Union[bytes, str],
        uri: str,
        content_type: str,
    ) -> float:
        """
        Return confidence 0-1 that this source can handle the content.
        
        This method should be fast and avoid full parsing. It's used to
        select the best handler for given content.
        
        Args:
            content: Raw content (bytes or string)
            uri: Source URI (file path or URL)
            content_type: MIME type if known (may be empty)
            
        Returns:
            Confidence score from 0.0 to 1.0:
            - 0.0: Cannot handle this content
            - 0.5: Possibly can handle (heuristic match)
            - 0.9+: High confidence (extension/content-type match)
        """
        ...
    
    def parse(
        self,
        content: Union[bytes, str],
        uri: str,
    ) -> ParsedSpec:
        """
        Parse content into a ParsedSpec.
        
        This method performs full parsing and extraction. It should:
        - Convert content to appropriate data structures
        - Set source_type correctly
        - Populate metadata with useful information
        - Record errors/warnings for non-fatal issues
        
        Args:
            content: Raw content (bytes or string)
            uri: Source URI for error messages and naming
            
        Returns:
            ParsedSpec with:
            - source_type: SourceType.API or SourceType.FILE
            - data: Parsed specification (dict or domain object)
            - metadata: Parsing metadata
            - errors: Any parsing errors
        """
        ...


@runtime_checkable
class StreamingSpecSource(Protocol):
    """
    Extended protocol for streaming-capable spec source handlers.
    
    Sources implementing this protocol support TRUE STREAMING:
    - detect_from_handle(): Detect from ContentHandle (may sample)
    - parse_from_handle(): Parse WITHOUT loading entire file into memory
    
    This enables processing of large files (100MB+) with constant memory.
    
    Example implementation:
    
        class CSVSource:
            # Standard SpecSource methods (required)
            def detect(self, content, uri, content_type) -> float:
                ...
            
            def parse(self, content, uri) -> ParsedSpec:
                ...
            
            # Streaming methods (for large files)
            def detect_from_handle(self, handle, uri, content_type) -> float:
                # Can sample first N bytes without loading entire file
                with handle.open_bytes() as f:
                    sample = f.read(8192)  # 8KB sample
                return self._detect_from_sample(sample, uri, content_type)
            
            def parse_from_handle(self, handle, uri) -> ParsedSpec:
                # TRUE STREAMING: iterate line-by-line
                with handle.open_text() as f:
                    reader = csv.reader(f)
                    for row in reader:
                        process(row)  # Row-by-row, not all in memory
    
    Detection (detect_from_handle):
        Router will first call detect_from_handle() if available.
        Implementation should:
        - Sample first N bytes (e.g., 8KB)
        - Check extension/content-type
        - NOT load entire file
    
    Parsing (parse_from_handle):
        Router will call parse_from_handle() for sources that support it.
        Implementation MUST:
        - Use handle.open_text() or handle.open_bytes()
        - Process incrementally (line-by-line, chunk-by-chunk)
        - NOT call handle.read_bytes() or handle.read_text()
    """
    
    def detect_from_handle(
        self,
        handle: "ContentHandle",
        uri: str,
        content_type: str,
    ) -> float:
        """
        Return confidence 0-1 that this source can handle the content.
        
        This method should sample the handle without loading full content.
        
        Args:
            handle: ContentHandle for streaming access
            uri: Source URI (file path or URL)
            content_type: MIME type if known (may be empty)
            
        Returns:
            Confidence score from 0.0 to 1.0
        """
        ...
    
    def parse_from_handle(
        self,
        handle: "ContentHandle",
        uri: str,
    ) -> ParsedSpec:
        """
        Parse content from handle WITHOUT loading entire file into memory.
        
        This is the TRUE STREAMING parse method. It MUST:
        - Use handle.open_text() or handle.open_bytes() for streaming
        - Process incrementally (line-by-line, chunk-by-chunk)
        - NOT call handle.read_bytes() or handle.read_text()
        
        For cancellation/timeout support, check ProcessingContext in the
        inner loop.
        
        Args:
            handle: ContentHandle for streaming access
            uri: Source URI for error messages and naming
            
        Returns:
            ParsedSpec with parsed content
        """
        ...


def supports_streaming(source: SpecSource) -> bool:
    """
    Check if a source implements the streaming protocol.
    
    Args:
        source: A SpecSource implementation
        
    Returns:
        True if source has detect_from_handle() and parse_from_handle()
    """
    return (
        hasattr(source, "detect_from_handle")
        and hasattr(source, "parse_from_handle")
        and callable(getattr(source, "detect_from_handle"))
        and callable(getattr(source, "parse_from_handle"))
    )


# Type alias for clarity
ContentType = Union[bytes, str]


def normalize_content(content: ContentType, encoding: str = "utf-8") -> str:
    """
    Normalize content to string.
    
    Args:
        content: bytes or str content
        encoding: Encoding to use for bytes (default: utf-8)
        
    Returns:
        String content
    """
    if isinstance(content, bytes):
        return content.decode(encoding, errors="ignore")
    return content


def normalize_content_bytes(content: ContentType, encoding: str = "utf-8") -> bytes:
    """
    Normalize content to bytes.
    
    Args:
        content: bytes or str content
        encoding: Encoding to use for str (default: utf-8)
        
    Returns:
        Bytes content
    """
    if isinstance(content, str):
        return content.encode(encoding)
    return content


def extract_name_from_uri(uri: str) -> str:
    """
    Extract a clean name from a URI.
    
    Examples:
        "file:///path/to/customers.csv" -> "customers"
        "https://api.example.com/data.json" -> "data"
        "/tmp/my_file.xlsx" -> "my_file"
    
    Args:
        uri: File path or URL
        
    Returns:
        Clean name (without extension)
    """
    from pathlib import Path
    import re
    
    # Handle URLs
    if "://" in uri:
        uri = uri.split("://", 1)[1]
    
    # Get filename
    name = Path(uri).stem
    
    # Clean up name
    name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    name = re.sub(r"_+", "_", name)
    name = name.strip("_").lower()
    
    return name or "unnamed_spec"


__all__ = [
    "SourceType",
    "ParsedSpec",
    "SpecSource",
    "StreamingSpecSource",
    "supports_streaming",
    "ContentType",
    "normalize_content",
    "normalize_content_bytes",
    "extract_name_from_uri",
]
