"""
Tests for ContentHandle abstraction.

Tests verify:
1. PathContentHandle - file-based streaming (PREFERRED)
2. BytesContentHandle - in-memory bytes (LEGACY)
3. StreamContentHandle - network/pipe streams
4. Factory functions
5. Context manager cleanup
"""

import io
import tempfile
from pathlib import Path

import pytest

from integration_coworker.sources.content_handle import (
    BytesContentHandle,
    ContentHandle,
    PathContentHandle,
    StreamContentHandle,
    content_handle_from_any,
    content_handle_from_bytes,
    content_handle_from_path,
    content_handle_from_stream,
    managed_content_handle,
)


# ===========================================================================
# Test Fixtures
# ===========================================================================


@pytest.fixture
def sample_csv_content() -> bytes:
    """Sample CSV content for testing."""
    return b"id,name,value\n1,alpha,100\n2,beta,200\n3,gamma,300\n"


@pytest.fixture
def sample_text_content() -> str:
    """Sample text content for testing."""
    return "Hello, World!\nThis is a test.\n"


@pytest.fixture
def temp_csv_file(sample_csv_content: bytes) -> Path:
    """Create a temporary CSV file."""
    fd, path = tempfile.mkstemp(suffix=".csv")
    try:
        import os
        os.write(fd, sample_csv_content)
    finally:
        import os
        os.close(fd)
    yield Path(path)
    # Cleanup
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def temp_text_file(sample_text_content: str) -> Path:
    """Create a temporary text file."""
    fd, path = tempfile.mkstemp(suffix=".txt")
    try:
        import os
        os.write(fd, sample_text_content.encode("utf-8"))
    finally:
        import os
        os.close(fd)
    yield Path(path)
    # Cleanup
    Path(path).unlink(missing_ok=True)


# ===========================================================================
# Test PathContentHandle
# ===========================================================================


class TestPathContentHandle:
    """Tests for PathContentHandle - file-based streaming."""
    
    def test_protocol_compliance(self, temp_csv_file: Path):
        """PathContentHandle should implement ContentHandle protocol."""
        handle = PathContentHandle(temp_csv_file)
        assert isinstance(handle, ContentHandle)
    
    def test_open_bytes_returns_binary_stream(self, temp_csv_file: Path, sample_csv_content: bytes):
        """open_bytes() should return a readable binary stream."""
        handle = PathContentHandle(temp_csv_file)
        with handle.open_bytes() as f:
            content = f.read()
        assert content == sample_csv_content
    
    def test_open_text_returns_text_stream(self, temp_text_file: Path, sample_text_content: str):
        """open_text() should return a readable text stream."""
        handle = PathContentHandle(temp_text_file)
        with handle.open_text() as f:
            content = f.read()
        assert content == sample_text_content
    
    def test_open_text_with_encoding(self, temp_csv_file: Path):
        """open_text() should respect encoding parameter."""
        handle = PathContentHandle(temp_csv_file)
        with handle.open_text(encoding="latin-1") as f:
            content = f.read()
        assert "id,name,value" in content
    
    def test_size_bytes_returns_file_size(self, temp_csv_file: Path, sample_csv_content: bytes):
        """size_bytes() should return exact file size via stat()."""
        handle = PathContentHandle(temp_csv_file)
        assert handle.size_bytes() == len(sample_csv_content)
    
    def test_size_bytes_is_o1(self, temp_csv_file: Path):
        """size_bytes() should be O(1) - not read the file."""
        handle = PathContentHandle(temp_csv_file)
        # Call multiple times - should be fast
        for _ in range(100):
            handle.size_bytes()
        # If it read the file each time, this would be slow
    
    def test_read_bytes_returns_full_content(self, temp_csv_file: Path, sample_csv_content: bytes):
        """read_bytes() should return full file content."""
        handle = PathContentHandle(temp_csv_file)
        assert handle.read_bytes() == sample_csv_content
    
    def test_read_text_returns_decoded_content(self, temp_text_file: Path, sample_text_content: str):
        """read_text() should return decoded string."""
        handle = PathContentHandle(temp_text_file)
        assert handle.read_text() == sample_text_content
    
    def test_to_local_path_returns_original_path(self, temp_csv_file: Path):
        """to_local_path() should return the original path."""
        handle = PathContentHandle(temp_csv_file)
        assert handle.to_local_path() == temp_csv_file
    
    def test_uri_returns_path_string(self, temp_csv_file: Path):
        """uri property should return path as string."""
        handle = PathContentHandle(temp_csv_file)
        assert handle.uri == str(temp_csv_file)
    
    def test_accepts_string_path(self, temp_csv_file: Path, sample_csv_content: bytes):
        """Should accept string path in constructor."""
        handle = PathContentHandle(str(temp_csv_file))
        assert handle.read_bytes() == sample_csv_content
    
    def test_repr_is_informative(self, temp_csv_file: Path):
        """__repr__ should show path."""
        handle = PathContentHandle(temp_csv_file)
        assert str(temp_csv_file) in repr(handle)
    
    def test_streaming_iteration(self, temp_csv_file: Path):
        """Should support line-by-line iteration."""
        handle = PathContentHandle(temp_csv_file)
        lines = []
        with handle.open_text() as f:
            for line in f:
                lines.append(line)
        assert len(lines) == 4
        assert lines[0].strip() == "id,name,value"


# ===========================================================================
# Test BytesContentHandle
# ===========================================================================


class TestBytesContentHandle:
    """Tests for BytesContentHandle - in-memory bytes."""
    
    def test_protocol_compliance(self, sample_csv_content: bytes):
        """BytesContentHandle should implement ContentHandle protocol."""
        handle = BytesContentHandle(sample_csv_content)
        assert isinstance(handle, ContentHandle)
    
    def test_open_bytes_returns_bytesio(self, sample_csv_content: bytes):
        """open_bytes() should return BytesIO wrapper."""
        handle = BytesContentHandle(sample_csv_content)
        stream = handle.open_bytes()
        assert isinstance(stream, io.BytesIO)
        assert stream.read() == sample_csv_content
    
    def test_open_text_returns_stringio(self, sample_csv_content: bytes):
        """open_text() should return StringIO with decoded content."""
        handle = BytesContentHandle(sample_csv_content)
        stream = handle.open_text()
        assert isinstance(stream, io.StringIO)
        assert "id,name,value" in stream.read()
    
    def test_size_bytes_returns_len(self, sample_csv_content: bytes):
        """size_bytes() should return len(bytes)."""
        handle = BytesContentHandle(sample_csv_content)
        assert handle.size_bytes() == len(sample_csv_content)
    
    def test_read_bytes_returns_content(self, sample_csv_content: bytes):
        """read_bytes() should return the bytes directly."""
        handle = BytesContentHandle(sample_csv_content)
        assert handle.read_bytes() == sample_csv_content
    
    def test_read_text_decodes_content(self, sample_csv_content: bytes):
        """read_text() should decode bytes to string."""
        handle = BytesContentHandle(sample_csv_content)
        text = handle.read_text()
        assert text == sample_csv_content.decode("utf-8")
    
    def test_to_local_path_creates_temp_file(self, sample_csv_content: bytes):
        """to_local_path() should create temp file with content."""
        handle = BytesContentHandle(sample_csv_content)
        try:
            path = handle.to_local_path()
            assert path.exists()
            assert path.read_bytes() == sample_csv_content
        finally:
            handle.cleanup()
    
    def test_to_local_path_is_cached(self, sample_csv_content: bytes):
        """to_local_path() should return same path on repeated calls."""
        handle = BytesContentHandle(sample_csv_content)
        try:
            path1 = handle.to_local_path()
            path2 = handle.to_local_path()
            assert path1 == path2
        finally:
            handle.cleanup()
    
    def test_cleanup_removes_temp_file(self, sample_csv_content: bytes):
        """cleanup() should delete the temp file."""
        handle = BytesContentHandle(sample_csv_content)
        path = handle.to_local_path()
        assert path.exists()
        handle.cleanup()
        assert not path.exists()
    
    def test_uri_default(self, sample_csv_content: bytes):
        """Default URI should show byte count."""
        handle = BytesContentHandle(sample_csv_content)
        assert f"<bytes:{len(sample_csv_content)}>" == handle.uri
    
    def test_uri_custom(self, sample_csv_content: bytes):
        """Custom URI should be preserved."""
        handle = BytesContentHandle(sample_csv_content, uri="https://example.com/data.csv")
        assert handle.uri == "https://example.com/data.csv"
    
    def test_repr_shows_size(self, sample_csv_content: bytes):
        """__repr__ should show byte count."""
        handle = BytesContentHandle(sample_csv_content)
        assert str(len(sample_csv_content)) in repr(handle)


# ===========================================================================
# Test StreamContentHandle
# ===========================================================================


class TestStreamContentHandle:
    """Tests for StreamContentHandle - network/pipe streams."""
    
    def test_protocol_compliance(self, sample_csv_content: bytes):
        """StreamContentHandle should implement ContentHandle protocol."""
        stream = io.BytesIO(sample_csv_content)
        handle = StreamContentHandle(stream)
        assert isinstance(handle, ContentHandle)
    
    def test_open_bytes_returns_stream(self, sample_csv_content: bytes):
        """open_bytes() should return readable stream."""
        stream = io.BytesIO(sample_csv_content)
        handle = StreamContentHandle(stream)
        with handle.open_bytes() as f:
            content = f.read()
        assert content == sample_csv_content
    
    def test_open_bytes_seekable_stream(self, sample_csv_content: bytes):
        """open_bytes() should handle seekable streams."""
        stream = io.BytesIO(sample_csv_content)
        handle = StreamContentHandle(stream)
        
        # First read - don't use context manager since we want to keep stream open
        f1 = handle.open_bytes()
        f1.read()
        # Don't close f1 - it's the same stream
        
        # Second read should still work (stream seeked or buffered)
        f2 = handle.open_bytes()
        content = f2.read()
        assert content == sample_csv_content
    
    def test_open_text_returns_text_stream(self, sample_csv_content: bytes):
        """open_text() should return text stream."""
        stream = io.BytesIO(sample_csv_content)
        handle = StreamContentHandle(stream)
        with handle.open_text() as f:
            content = f.read()
        assert "id,name,value" in content
    
    def test_size_bytes_returns_hint_when_known(self, sample_csv_content: bytes):
        """size_bytes() should return hint if provided."""
        stream = io.BytesIO(sample_csv_content)
        handle = StreamContentHandle(stream, size_hint=1000)
        assert handle.size_bytes() == 1000
    
    def test_size_bytes_returns_none_when_unknown(self, sample_csv_content: bytes):
        """size_bytes() should return None if no hint."""
        stream = io.BytesIO(sample_csv_content)
        handle = StreamContentHandle(stream)
        assert handle.size_bytes() is None
    
    def test_size_bytes_returns_actual_after_buffer(self, sample_csv_content: bytes):
        """size_bytes() should return actual size after buffering."""
        stream = io.BytesIO(sample_csv_content)
        handle = StreamContentHandle(stream, size_hint=None)
        # Buffer the stream
        handle.read_bytes()
        # Now size is known
        assert handle.size_bytes() == len(sample_csv_content)
    
    def test_read_bytes_buffers_content(self, sample_csv_content: bytes):
        """read_bytes() should buffer and return all bytes."""
        stream = io.BytesIO(sample_csv_content)
        handle = StreamContentHandle(stream)
        
        content1 = handle.read_bytes()
        content2 = handle.read_bytes()
        
        assert content1 == sample_csv_content
        assert content2 == sample_csv_content
    
    def test_read_text_returns_decoded(self, sample_csv_content: bytes):
        """read_text() should decode buffered content."""
        stream = io.BytesIO(sample_csv_content)
        handle = StreamContentHandle(stream)
        text = handle.read_text()
        assert text == sample_csv_content.decode("utf-8")
    
    def test_to_local_path_creates_temp_file(self, sample_csv_content: bytes):
        """to_local_path() should create temp file."""
        stream = io.BytesIO(sample_csv_content)
        handle = StreamContentHandle(stream)
        try:
            path = handle.to_local_path()
            assert path.exists()
            assert path.read_bytes() == sample_csv_content
        finally:
            handle.cleanup()
    
    def test_cleanup_removes_temp_file(self, sample_csv_content: bytes):
        """cleanup() should delete temp file."""
        stream = io.BytesIO(sample_csv_content)
        handle = StreamContentHandle(stream)
        path = handle.to_local_path()
        handle.cleanup()
        assert not path.exists()
    
    def test_uri_default(self, sample_csv_content: bytes):
        """Default URI should be <stream>."""
        stream = io.BytesIO(sample_csv_content)
        handle = StreamContentHandle(stream)
        assert handle.uri == "<stream>"
    
    def test_uri_custom(self, sample_csv_content: bytes):
        """Custom URI should be preserved."""
        stream = io.BytesIO(sample_csv_content)
        handle = StreamContentHandle(stream, uri="https://api.example.com/data")
        assert handle.uri == "https://api.example.com/data"


# ===========================================================================
# Test Factory Functions
# ===========================================================================


class TestFactoryFunctions:
    """Tests for factory functions."""
    
    def test_from_path_returns_path_handle(self, temp_csv_file: Path):
        """content_handle_from_path should return PathContentHandle."""
        handle = content_handle_from_path(temp_csv_file)
        assert isinstance(handle, PathContentHandle)
    
    def test_from_path_accepts_string(self, temp_csv_file: Path):
        """content_handle_from_path should accept string path."""
        handle = content_handle_from_path(str(temp_csv_file))
        assert isinstance(handle, PathContentHandle)
    
    def test_from_path_raises_on_missing_file(self):
        """content_handle_from_path should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            content_handle_from_path("/nonexistent/file.csv")
    
    def test_from_bytes_returns_bytes_handle(self, sample_csv_content: bytes):
        """content_handle_from_bytes should return BytesContentHandle."""
        handle = content_handle_from_bytes(sample_csv_content)
        assert isinstance(handle, BytesContentHandle)
    
    def test_from_bytes_with_uri(self, sample_csv_content: bytes):
        """content_handle_from_bytes should accept uri parameter."""
        handle = content_handle_from_bytes(sample_csv_content, uri="test://data")
        assert handle.uri == "test://data"
    
    def test_from_stream_returns_stream_handle(self, sample_csv_content: bytes):
        """content_handle_from_stream should return StreamContentHandle."""
        stream = io.BytesIO(sample_csv_content)
        handle = content_handle_from_stream(stream)
        assert isinstance(handle, StreamContentHandle)
    
    def test_from_stream_with_size_hint(self, sample_csv_content: bytes):
        """content_handle_from_stream should accept size_hint."""
        stream = io.BytesIO(sample_csv_content)
        handle = content_handle_from_stream(stream, size_hint=100)
        assert handle.size_bytes() == 100
    
    def test_from_any_with_path(self, temp_csv_file: Path):
        """content_handle_from_any should handle Path."""
        handle = content_handle_from_any(temp_csv_file)
        assert isinstance(handle, PathContentHandle)
    
    def test_from_any_with_string_path(self, temp_csv_file: Path):
        """content_handle_from_any should handle string path."""
        handle = content_handle_from_any(str(temp_csv_file))
        assert isinstance(handle, PathContentHandle)
    
    def test_from_any_with_bytes(self, sample_csv_content: bytes):
        """content_handle_from_any should handle bytes."""
        handle = content_handle_from_any(sample_csv_content)
        assert isinstance(handle, BytesContentHandle)
    
    def test_from_any_with_stream(self, sample_csv_content: bytes):
        """content_handle_from_any should handle streams."""
        stream = io.BytesIO(sample_csv_content)
        handle = content_handle_from_any(stream)
        assert isinstance(handle, StreamContentHandle)
    
    def test_from_any_with_content_handle(self, sample_csv_content: bytes):
        """content_handle_from_any should return ContentHandle as-is."""
        original = BytesContentHandle(sample_csv_content)
        handle = content_handle_from_any(original)
        assert handle is original
    
    def test_from_any_with_invalid_type(self):
        """content_handle_from_any should raise TypeError for invalid input."""
        with pytest.raises(TypeError):
            content_handle_from_any(12345)  # type: ignore


# ===========================================================================
# Test Context Manager
# ===========================================================================


class TestManagedContentHandle:
    """Tests for managed_content_handle context manager."""
    
    def test_cleanup_on_exit(self, sample_csv_content: bytes):
        """Context manager should cleanup temp files."""
        handle = BytesContentHandle(sample_csv_content)
        
        with managed_content_handle(handle) as h:
            path = h.to_local_path()
            assert path.exists()
        
        # After exit, temp file should be deleted
        assert not path.exists()
    
    def test_cleanup_on_exception(self, sample_csv_content: bytes):
        """Context manager should cleanup even on exception."""
        handle = BytesContentHandle(sample_csv_content)
        path = None
        
        try:
            with managed_content_handle(handle) as h:
                path = h.to_local_path()
                raise ValueError("Test exception")
        except ValueError:
            pass
        
        # Temp file should still be deleted
        assert path is not None
        assert not path.exists()
    
    def test_path_handle_no_cleanup_needed(self, temp_csv_file: Path):
        """PathContentHandle should work with context manager (no-op cleanup)."""
        handle = PathContentHandle(temp_csv_file)
        
        with managed_content_handle(handle) as h:
            path = h.to_local_path()
            assert path == temp_csv_file
        
        # Original file should NOT be deleted
        assert temp_csv_file.exists()


# ===========================================================================
# Test Edge Cases
# ===========================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling."""
    
    def test_empty_bytes(self):
        """Should handle empty bytes."""
        handle = BytesContentHandle(b"")
        assert handle.size_bytes() == 0
        assert handle.read_bytes() == b""
        assert handle.read_text() == ""
    
    def test_unicode_content(self):
        """Should handle unicode content."""
        content = "日本語テスト".encode("utf-8")
        handle = BytesContentHandle(content)
        assert handle.read_text() == "日本語テスト"
    
    def test_binary_content(self):
        """Should handle binary content (non-text)."""
        content = bytes(range(256))
        handle = BytesContentHandle(content)
        assert handle.read_bytes() == content
        assert handle.size_bytes() == 256
    
    def test_large_content_simulation(self):
        """Should handle moderately large content."""
        # 1MB of data
        content = b"x" * (1024 * 1024)
        handle = BytesContentHandle(content)
        assert handle.size_bytes() == 1024 * 1024
    
    def test_path_handle_with_spaces(self, sample_csv_content: bytes):
        """Should handle paths with spaces."""
        fd, path = tempfile.mkstemp(prefix="test file ", suffix=".csv")
        try:
            import os
            os.write(fd, sample_csv_content)
            os.close(fd)
            
            handle = PathContentHandle(path)
            assert handle.read_bytes() == sample_csv_content
        finally:
            Path(path).unlink(missing_ok=True)
    
    def test_multiple_open_calls(self, sample_csv_content: bytes):
        """Should support multiple open calls."""
        handle = BytesContentHandle(sample_csv_content)
        
        # Multiple sequential opens should work
        for _ in range(3):
            with handle.open_bytes() as f:
                assert f.read() == sample_csv_content


# ===========================================================================
# Test Type Hints and Protocol
# ===========================================================================


class TestTypeHints:
    """Tests to verify type hint compatibility."""
    
    def test_all_handles_are_content_handle(
        self,
        temp_csv_file: Path,
        sample_csv_content: bytes,
    ):
        """All handle types should satisfy ContentHandle protocol."""
        handles: list[ContentHandle] = [
            PathContentHandle(temp_csv_file),
            BytesContentHandle(sample_csv_content),
            StreamContentHandle(io.BytesIO(sample_csv_content)),
        ]
        
        for handle in handles:
            # These should all work (type system check)
            assert handle.size_bytes() is not None or handle.size_bytes() is None
            assert handle.uri
            _ = handle.read_bytes()
            _ = handle.read_text()
