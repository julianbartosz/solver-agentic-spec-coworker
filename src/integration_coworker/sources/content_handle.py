"""
ContentHandle abstraction for streaming file processing.

This module provides a unified interface for accessing file content
from multiple sources (paths, streams, bytes) without requiring
the entire content to be loaded into memory.

Handle Types:
- PathContentHandle: File on disk - can stream directly
- StreamContentHandle: Network stream - can stream if Content-Length known
- BytesContentHandle: In-memory bytes (legacy, not scalable)

All sources and routing should use ContentHandle, not raw bytes.
This enables:
1. Pre-load size checking (os.path.getsize before reading)
2. True streaming (open file, yield chunks)
3. Memory-efficient processing of large files

Usage:
    # From path (best for streaming)
    handle = PathContentHandle(Path("/data/large.csv"))
    
    # Pre-check size before loading
    if handle.size_bytes() > MAX_SIZE:
        raise FileTooLargeError(...)
    
    # Stream content
    with handle.open_text() as f:
        for line in f:
            process(line)
    
    # Or get bytes if needed (legacy compatibility)
    content = handle.read_bytes()
"""

from __future__ import annotations

import io
import os
import tempfile
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    BinaryIO,
    Iterator,
    Optional,
    Protocol,
    TextIO,
    Union,
    runtime_checkable,
)

if TYPE_CHECKING:
    from io import BufferedReader, TextIOWrapper


# ===========================================================================
# Protocol Definition
# ===========================================================================


@runtime_checkable
class ContentHandle(Protocol):
    """
    Protocol for accessing file content in a streaming-friendly way.
    
    All implementations must support:
    - open_bytes(): Get a binary stream (seekable preferred)
    - open_text(): Get a text stream with encoding
    - size_bytes(): Get size without loading content (if possible)
    - read_bytes(): Convenience to load all bytes (legacy compatibility)
    - read_text(): Convenience to load all text (legacy compatibility)
    - to_local_path(): Get/create a local file path (for libs requiring files)
    """
    
    def open_bytes(self) -> BinaryIO:
        """
        Open content as a binary stream.
        
        Returns:
            Binary stream for reading. Caller must close it.
            May or may not be seekable depending on implementation.
        """
        ...
    
    def open_text(self, encoding: str = "utf-8") -> TextIO:
        """
        Open content as a text stream.
        
        Args:
            encoding: Text encoding to use (default: utf-8)
            
        Returns:
            Text stream for reading. Caller must close it.
        """
        ...
    
    def size_bytes(self) -> Optional[int]:
        """
        Get content size in bytes without loading content.
        
        Returns:
            Size in bytes, or None if unknown (e.g., unseekable stream).
            For PathContentHandle, this is O(1) stat() call.
            For BytesContentHandle, this is len(bytes).
            For StreamContentHandle, this may be None or Content-Length.
        """
        ...
    
    def read_bytes(self) -> bytes:
        """
        Load entire content as bytes.
        
        WARNING: This defeats streaming. Use open_bytes() instead.
        Provided for backwards compatibility only.
        
        Returns:
            Full content as bytes.
        """
        ...
    
    def read_text(self, encoding: str = "utf-8") -> str:
        """
        Load entire content as text.
        
        WARNING: This defeats streaming. Use open_text() instead.
        Provided for backwards compatibility only.
        
        Args:
            encoding: Text encoding to use
            
        Returns:
            Full content as string.
        """
        ...
    
    def to_local_path(self) -> Path:
        """
        Get or create a local file path for content.
        
        Some libraries (e.g., openpyxl) require a file path.
        For PathContentHandle, this returns the existing path.
        For other handles, this creates a temp file and returns its path.
        
        WARNING: For non-path handles, caller is responsible for cleanup.
        
        Returns:
            Path to a file containing the content.
        """
        ...
    
    @property
    def uri(self) -> str:
        """
        Get a URI/identifier for this content.
        
        Returns:
            Path string, URL, or description (e.g., "<bytes:1234>")
        """
        ...


# ===========================================================================
# PathContentHandle - Files on disk (PREFERRED for streaming)
# ===========================================================================


class PathContentHandle:
    """
    ContentHandle for files on the local filesystem.
    
    This is the PREFERRED handle type because:
    - Size is O(1) via stat()
    - Content can be streamed directly
    - No temp files needed
    - Libraries that need paths work directly
    
    Example:
        handle = PathContentHandle(Path("/data/large.csv"))
        print(f"Size: {handle.size_bytes()} bytes")
        
        with handle.open_text() as f:
            reader = csv.reader(f)
            for row in reader:
                process(row)
    """
    
    __slots__ = ("_path", "_encoding")
    
    def __init__(self, path: Union[str, Path], encoding: str = "utf-8"):
        """
        Create handle for a file path.
        
        Args:
            path: Path to the file
            encoding: Default encoding for text operations
        """
        self._path = Path(path) if isinstance(path, str) else path
        self._encoding = encoding
    
    def open_bytes(self) -> BinaryIO:
        """Open file as binary stream."""
        return open(self._path, "rb")
    
    def open_text(self, encoding: str = "utf-8") -> TextIO:
        """Open file as text stream."""
        return open(self._path, "r", encoding=encoding or self._encoding)
    
    def size_bytes(self) -> int:
        """Get file size via stat() - O(1), no reading."""
        return self._path.stat().st_size
    
    def read_bytes(self) -> bytes:
        """Load entire file as bytes."""
        return self._path.read_bytes()
    
    def read_text(self, encoding: str = "utf-8") -> str:
        """Load entire file as text."""
        return self._path.read_text(encoding=encoding or self._encoding)
    
    def to_local_path(self) -> Path:
        """Return the path directly - no temp file needed."""
        return self._path
    
    @property
    def uri(self) -> str:
        """Return file path as string."""
        return str(self._path)
    
    def __repr__(self) -> str:
        return f"PathContentHandle({self._path!r})"


# ===========================================================================
# BytesContentHandle - In-memory bytes (LEGACY, not scalable)
# ===========================================================================


class BytesContentHandle:
    """
    ContentHandle for in-memory bytes.
    
    This is a LEGACY handle type for backwards compatibility.
    It defeats the purpose of streaming because the entire content
    is already in memory.
    
    Use PathContentHandle instead when possible.
    
    Example:
        # Legacy code that already has bytes
        content = read_from_network()
        handle = BytesContentHandle(content, uri="https://example.com/file.csv")
    """
    
    __slots__ = ("_content", "_uri", "_temp_path")
    
    def __init__(self, content: bytes, uri: str = "<bytes>"):
        """
        Create handle for in-memory bytes.
        
        Args:
            content: The bytes content
            uri: Optional identifier for logging/errors
        """
        self._content = content
        self._uri = uri
        self._temp_path: Optional[Path] = None
    
    def open_bytes(self) -> BinaryIO:
        """Wrap bytes in BytesIO."""
        return io.BytesIO(self._content)
    
    def open_text(self, encoding: str = "utf-8") -> TextIO:
        """Wrap bytes in StringIO after decoding."""
        return io.StringIO(self._content.decode(encoding))
    
    def size_bytes(self) -> int:
        """Return len(bytes)."""
        return len(self._content)
    
    def read_bytes(self) -> bytes:
        """Return the bytes directly."""
        return self._content
    
    def read_text(self, encoding: str = "utf-8") -> str:
        """Decode and return as string."""
        return self._content.decode(encoding)
    
    def to_local_path(self) -> Path:
        """
        Write to temp file and return path.
        
        WARNING: Temp file is NOT automatically cleaned up.
        Caller should delete when done.
        """
        if self._temp_path is None:
            # Create temp file with content
            fd, path = tempfile.mkstemp(suffix=".tmp")
            try:
                os.write(fd, self._content)
            finally:
                os.close(fd)
            self._temp_path = Path(path)
        return self._temp_path
    
    @property
    def uri(self) -> str:
        """Return the URI/identifier."""
        if self._uri == "<bytes>":
            return f"<bytes:{len(self._content)}>"
        return self._uri
    
    def cleanup(self) -> None:
        """Clean up any temp files created."""
        if self._temp_path is not None and self._temp_path.exists():
            self._temp_path.unlink()
            self._temp_path = None
    
    def __repr__(self) -> str:
        return f"BytesContentHandle({len(self._content)} bytes, uri={self._uri!r})"


# ===========================================================================
# StreamContentHandle - Network/pipe streams
# ===========================================================================


class StreamContentHandle:
    """
    ContentHandle for readable streams (network, pipes, etc.).
    
    This handle supports streaming from sources where the content
    arrives incrementally (e.g., HTTP response body).
    
    If size is known (e.g., Content-Length header), provide it
    for pre-load size checking.
    
    Example:
        response = requests.get(url, stream=True)
        handle = StreamContentHandle(
            response.raw,
            size_hint=int(response.headers.get("Content-Length", 0)),
            uri=url
        )
    """
    
    __slots__ = ("_stream", "_size_hint", "_uri", "_buffered", "_temp_path")
    
    def __init__(
        self,
        stream: BinaryIO,
        size_hint: Optional[int] = None,
        uri: str = "<stream>",
    ):
        """
        Create handle for a stream.
        
        Args:
            stream: Binary stream to read from
            size_hint: Optional size hint (e.g., Content-Length)
            uri: Optional identifier for logging/errors
        """
        self._stream = stream
        self._size_hint = size_hint
        self._uri = uri
        self._buffered: Optional[bytes] = None
        self._temp_path: Optional[Path] = None
    
    def _ensure_buffered(self) -> bytes:
        """Buffer the stream content (only once)."""
        if self._buffered is None:
            self._buffered = self._stream.read()
        return self._buffered
    
    def open_bytes(self) -> BinaryIO:
        """
        Get binary stream.
        
        If stream was already consumed, returns buffered content.
        """
        if self._buffered is not None:
            return io.BytesIO(self._buffered)
        
        # If stream is seekable, just seek to start
        if hasattr(self._stream, "seekable") and self._stream.seekable():
            self._stream.seek(0)
            return self._stream
        
        # Otherwise, buffer and return BytesIO
        return io.BytesIO(self._ensure_buffered())
    
    def open_text(self, encoding: str = "utf-8") -> TextIO:
        """Get text stream by wrapping open_bytes()."""
        return io.TextIOWrapper(self.open_bytes(), encoding=encoding)
    
    def size_bytes(self) -> Optional[int]:
        """
        Return size hint if known, else None.
        
        If stream was buffered, return actual size.
        """
        if self._buffered is not None:
            return len(self._buffered)
        return self._size_hint
    
    def read_bytes(self) -> bytes:
        """Buffer and return all bytes."""
        return self._ensure_buffered()
    
    def read_text(self, encoding: str = "utf-8") -> str:
        """Buffer and return as text."""
        return self._ensure_buffered().decode(encoding)
    
    def to_local_path(self) -> Path:
        """
        Write buffered content to temp file.
        
        WARNING: Temp file is NOT automatically cleaned up.
        """
        if self._temp_path is None:
            content = self._ensure_buffered()
            fd, path = tempfile.mkstemp(suffix=".tmp")
            try:
                os.write(fd, content)
            finally:
                os.close(fd)
            self._temp_path = Path(path)
        return self._temp_path
    
    @property
    def uri(self) -> str:
        """Return the URI/identifier."""
        return self._uri
    
    def cleanup(self) -> None:
        """Clean up any temp files created."""
        if self._temp_path is not None and self._temp_path.exists():
            self._temp_path.unlink()
            self._temp_path = None
    
    def __repr__(self) -> str:
        size_str = f"{self._size_hint} bytes" if self._size_hint else "unknown size"
        return f"StreamContentHandle({size_str}, uri={self._uri!r})"


# ===========================================================================
# Factory functions
# ===========================================================================


def content_handle_from_path(path: Union[str, Path]) -> PathContentHandle:
    """
    Create a ContentHandle from a file path.
    
    This is the PREFERRED factory for file processing.
    
    Args:
        path: Path to the file
        
    Returns:
        PathContentHandle for the file
        
    Raises:
        FileNotFoundError: If file doesn't exist
    """
    path = Path(path) if isinstance(path, str) else path
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return PathContentHandle(path)


def content_handle_from_bytes(content: bytes, uri: str = "<bytes>") -> BytesContentHandle:
    """
    Create a ContentHandle from bytes.
    
    WARNING: This defeats streaming. Use content_handle_from_path() instead.
    
    Args:
        content: The bytes content
        uri: Optional identifier
        
    Returns:
        BytesContentHandle wrapping the bytes
    """
    return BytesContentHandle(content, uri=uri)


def content_handle_from_stream(
    stream: BinaryIO,
    size_hint: Optional[int] = None,
    uri: str = "<stream>",
) -> StreamContentHandle:
    """
    Create a ContentHandle from a binary stream.
    
    Args:
        stream: Binary stream to read from
        size_hint: Optional size hint (e.g., Content-Length)
        uri: Optional identifier
        
    Returns:
        StreamContentHandle for the stream
    """
    return StreamContentHandle(stream, size_hint=size_hint, uri=uri)


def content_handle_from_any(
    source: Union[str, Path, bytes, BinaryIO, ContentHandle],
    uri: str = "",
    size_hint: Optional[int] = None,
) -> ContentHandle:
    """
    Create appropriate ContentHandle from various input types.
    
    This is a convenience factory that handles any input type:
    - str/Path: Creates PathContentHandle
    - bytes: Creates BytesContentHandle
    - BinaryIO: Creates StreamContentHandle
    - ContentHandle: Returns as-is
    
    Args:
        source: The content source
        uri: Optional URI (used for bytes/stream if not provided)
        size_hint: Optional size hint (for streams)
        
    Returns:
        Appropriate ContentHandle implementation
    """
    if isinstance(source, ContentHandle):
        return source
    
    if isinstance(source, (str, Path)):
        path = Path(source) if isinstance(source, str) else source
        if path.exists():
            return PathContentHandle(path)
        # If path doesn't exist, treat as content string
        return BytesContentHandle(str(source).encode("utf-8"), uri=uri or "<string>")
    
    if isinstance(source, bytes):
        return BytesContentHandle(source, uri=uri or "<bytes>")
    
    if hasattr(source, "read"):
        return StreamContentHandle(source, size_hint=size_hint, uri=uri or "<stream>")
    
    raise TypeError(f"Cannot create ContentHandle from {type(source)}")


# ===========================================================================
# Context manager for cleanup
# ===========================================================================


@contextmanager
def managed_content_handle(handle: ContentHandle) -> Iterator[ContentHandle]:
    """
    Context manager that cleans up temp files when done.
    
    Use this when you need to_local_path() and want automatic cleanup.
    
    Example:
        with managed_content_handle(BytesContentHandle(data)) as handle:
            path = handle.to_local_path()
            process_file(path)
        # Temp file automatically deleted
    """
    try:
        yield handle
    finally:
        if hasattr(handle, "cleanup"):
            handle.cleanup()


# ===========================================================================
# Exports
# ===========================================================================


__all__ = [
    # Protocol
    "ContentHandle",
    # Implementations
    "PathContentHandle",
    "BytesContentHandle", 
    "StreamContentHandle",
    # Factories
    "content_handle_from_path",
    "content_handle_from_bytes",
    "content_handle_from_stream",
    "content_handle_from_any",
    # Context manager
    "managed_content_handle",
]
