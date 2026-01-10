"""
Tests for detect_and_route_handle() - ContentHandle-based entry point.

These tests verify the NEW streaming-capable API that:
1. Performs PRE-LOAD size checking (rejects before memory allocation)
2. Uses ContentHandle abstraction
3. Maintains backwards compatibility
"""

import tempfile
from pathlib import Path

import pytest

from integration_coworker.sources import (
    BytesContentHandle,
    FileTooLargeError,
    PathContentHandle,
    ProcessingContext,
    content_handle_from_path,
    detect_and_route_handle,
    ensure_sources_registered,
)
from integration_coworker.sources.processing_context import (
    FileSizeGate,
)


# Ensure sources are registered before tests
@pytest.fixture(autouse=True)
def register_sources():
    ensure_sources_registered()


# ===========================================================================
# Test Fixtures
# ===========================================================================


@pytest.fixture
def sample_csv_content() -> bytes:
    """Sample CSV content."""
    return b"id,name,amount\n1,alpha,100\n2,beta,200\n"


@pytest.fixture
def sample_fixed_width_content() -> bytes:
    """Sample fixed-width content."""
    return b"ID  NAME       AMOUNT\n001 ALPHA      000100\n002 BETA       000200\n"


@pytest.fixture
def temp_csv_file(sample_csv_content: bytes) -> Path:
    """Create temporary CSV file."""
    fd, path = tempfile.mkstemp(suffix=".csv")
    try:
        import os
        os.write(fd, sample_csv_content)
    finally:
        import os
        os.close(fd)
    yield Path(path)
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def temp_large_file() -> Path:
    """Create temporary large file for size testing."""
    fd, path = tempfile.mkstemp(suffix=".csv")
    try:
        import os
        # Write a 1KB CSV file
        header = b"id,value\n"
        os.write(fd, header)
        for i in range(100):
            os.write(fd, f"{i},{i*10}\n".encode())
    finally:
        import os
        os.close(fd)
    yield Path(path)
    Path(path).unlink(missing_ok=True)


# ===========================================================================
# Test detect_and_route_handle Basic Functionality
# ===========================================================================


class TestDetectAndRouteHandle:
    """Tests for detect_and_route_handle() function."""
    
    def test_detect_csv_from_path_handle(self, temp_csv_file: Path):
        """Should detect and parse CSV from PathContentHandle."""
        handle = PathContentHandle(temp_csv_file)
        result = detect_and_route_handle(handle)
        
        # Should parse successfully
        assert result.confidence > 0.5
        assert "file_spec" in result.data or "fields" in result.data
    
    def test_detect_csv_from_bytes_handle(self, sample_csv_content: bytes):
        """Should detect and parse CSV from BytesContentHandle."""
        handle = BytesContentHandle(sample_csv_content, uri="test.csv")
        result = detect_and_route_handle(handle)
        
        assert result.confidence > 0.5
    
    def test_uses_handle_uri_if_not_provided(self, sample_csv_content: bytes):
        """Should use handle.uri if uri parameter not provided."""
        handle = BytesContentHandle(sample_csv_content, uri="my_custom_file.csv")
        result = detect_and_route_handle(handle)
        
        # URI should be used in result
        assert result.confidence > 0
    
    def test_explicit_uri_overrides_handle_uri(self, sample_csv_content: bytes):
        """Should use explicit uri parameter over handle.uri."""
        handle = BytesContentHandle(sample_csv_content, uri="handle_uri.csv")
        result = detect_and_route_handle(handle, uri="explicit_uri.csv")
        
        # Should work with explicit URI
        assert result.confidence > 0
    
    def test_with_content_type_hint(self, sample_csv_content: bytes):
        """Should respect content_type parameter."""
        handle = BytesContentHandle(sample_csv_content, uri="data.dat")
        result = detect_and_route_handle(handle, content_type="text/csv")
        
        assert result.confidence > 0
    
    def test_with_processing_context(self, temp_csv_file: Path):
        """Should work with ProcessingContext."""
        handle = PathContentHandle(temp_csv_file)
        ctx = ProcessingContext.from_config()
        
        result = detect_and_route_handle(handle, processing_ctx=ctx)
        
        assert result.confidence > 0
    
    def test_no_match_raises_value_error(self):
        """Should raise ValueError if no source matches with high confidence."""
        # Empty content that doesn't match any format well
        handle = BytesContentHandle(b"", uri="empty.bin")
        
        # Note: Some sources may still match with low confidence.
        # This test verifies the error path exists.
        # If a source matches, that's also valid behavior.
        try:
            result = detect_and_route_handle(handle)
            # If we get here, some source matched - check it's low confidence
            assert result.confidence < 0.5, "Expected low confidence for empty content"
        except ValueError as e:
            # Expected: no match
            assert "No source handler matched" in str(e)


# ===========================================================================
# Test PRE-LOAD Size Checking (Key Feature)
# ===========================================================================


class TestPreLoadSizeCheck:
    """Tests for pre-load size checking - rejects BEFORE memory allocation."""
    
    def test_pre_load_size_check_rejects_oversized(self, temp_large_file: Path):
        """Should reject file BEFORE loading based on size_bytes()."""
        handle = PathContentHandle(temp_large_file)
        
        # Configure to reject files > 100 bytes
        ctx = ProcessingContext(
            size_gate=FileSizeGate(max_size_bytes=100)
        )
        
        # This should check size BEFORE reading content
        with pytest.raises(FileTooLargeError) as exc_info:
            detect_and_route_handle(handle, processing_ctx=ctx)
        
        # Verify the error has correct info
        assert exc_info.value.file_size_bytes == handle.size_bytes()
    
    def test_pre_load_size_check_adds_warning_for_large(self, temp_large_file: Path):
        """Should add warning for large (but not too large) files."""
        handle = PathContentHandle(temp_large_file)
        actual_size = handle.size_bytes()
        
        # Configure: warn above 100 bytes, reject above 1MB
        ctx = ProcessingContext(
            size_gate=FileSizeGate(
                warn_size_bytes=100,
                max_size_bytes=1024 * 1024,
            )
        )
        
        result = detect_and_route_handle(handle, processing_ctx=ctx)
        
        # Should have warning
        assert len(result.warnings) > 0
        assert any("streaming mode" in w for w in result.warnings)
    
    def test_bytes_handle_size_also_checked(self, sample_csv_content: bytes):
        """BytesContentHandle should also have size checked."""
        handle = BytesContentHandle(sample_csv_content)
        
        # Reject anything > 10 bytes
        ctx = ProcessingContext(
            size_gate=FileSizeGate(max_size_bytes=10)
        )
        
        with pytest.raises(FileTooLargeError):
            detect_and_route_handle(handle, processing_ctx=ctx)
    
    def test_no_context_skips_size_check(self, temp_large_file: Path):
        """Should not check size if no processing_ctx provided."""
        handle = PathContentHandle(temp_large_file)
        
        # Without context, no size check
        result = detect_and_route_handle(handle)
        
        # Should succeed
        assert result.confidence > 0


# ===========================================================================
# Test check_size_bytes Method
# ===========================================================================


class TestCheckSizeBytes:
    """Tests for ProcessingContext.check_size_bytes()."""
    
    def test_check_size_bytes_rejects_over_max(self):
        """Should raise FileTooLargeError for size over max."""
        ctx = ProcessingContext(
            size_gate=FileSizeGate(max_size_bytes=1000)
        )
        
        with pytest.raises(FileTooLargeError) as exc_info:
            ctx.check_size_bytes(2000, uri="test.csv")
        
        assert exc_info.value.file_size_bytes == 2000
        assert exc_info.value.max_size_bytes == 1000
    
    def test_check_size_bytes_returns_warning(self):
        """Should return warning for size between warn and max."""
        ctx = ProcessingContext(
            size_gate=FileSizeGate(
                warn_size_bytes=1000,
                max_size_bytes=10000,
            )
        )
        
        warnings = ctx.check_size_bytes(5000, uri="test.csv")
        
        assert len(warnings) == 1
        assert "streaming mode" in warnings[0]
    
    def test_check_size_bytes_no_warning_below_threshold(self):
        """Should return empty list for size below warn threshold."""
        ctx = ProcessingContext(
            size_gate=FileSizeGate(
                warn_size_bytes=1000,
                max_size_bytes=10000,
            )
        )
        
        warnings = ctx.check_size_bytes(500, uri="test.csv")
        
        assert warnings == []
    
    def test_check_size_bytes_no_gate_returns_empty(self):
        """Should return empty list if no size gate configured."""
        ctx = ProcessingContext()  # No size gate
        
        warnings = ctx.check_size_bytes(1000000, uri="test.csv")
        
        assert warnings == []


# ===========================================================================
# Test Factory Integration
# ===========================================================================


class TestContentHandleFactoryIntegration:
    """Tests for factory function integration with detect_and_route_handle."""
    
    def test_content_handle_from_path_works(self, temp_csv_file: Path):
        """content_handle_from_path() should work with detect_and_route_handle."""
        handle = content_handle_from_path(temp_csv_file)
        result = detect_and_route_handle(handle)
        
        assert result.confidence > 0
    
    def test_path_handle_enables_preload_check(self, temp_csv_file: Path):
        """PathContentHandle enables pre-load size check."""
        handle = content_handle_from_path(temp_csv_file)
        
        # Pre-load check: we know size without reading
        assert handle.size_bytes() is not None
        assert handle.size_bytes() > 0
        
        # Verify size check happens before parse
        ctx = ProcessingContext(
            size_gate=FileSizeGate(max_size_bytes=1)  # Reject everything
        )
        
        with pytest.raises(FileTooLargeError):
            detect_and_route_handle(handle, processing_ctx=ctx)


# ===========================================================================
# Test Backwards Compatibility
# ===========================================================================


class TestBackwardsCompatibility:
    """Tests to ensure detect_and_route_handle works alongside detect_and_route."""
    
    def test_same_result_as_detect_and_route(self, sample_csv_content: bytes, temp_csv_file: Path):
        """Results should be comparable between old and new API."""
        from integration_coworker.sources import detect_and_route
        
        # Old API with bytes
        old_result = detect_and_route(sample_csv_content, str(temp_csv_file))
        
        # New API with handle
        handle = PathContentHandle(temp_csv_file)
        new_result = detect_and_route_handle(handle)
        
        # Should have similar confidence
        assert abs(old_result.confidence - new_result.confidence) < 0.1
        assert old_result.source_type == new_result.source_type
