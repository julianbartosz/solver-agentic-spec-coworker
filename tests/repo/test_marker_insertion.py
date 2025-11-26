"""
Tests for marker-based block insertion in repo files.

Verifies the idempotent insertion of auto-generated blocks
into router and settings files per Appendix G.
"""
import pytest
from integration_coworker.repo.helpers import (
    upsert_block_between_markers,
    generate_router_block,
    generate_settings_block,
)


def test_upsert_block_when_markers_missing():
    """Test that markers + block are appended when markers don't exist."""
    original = "# Existing content\nrouter = APIRouter()\n"
    start_marker = "# BEGIN AUTO-GENERATED"
    end_marker = "# END AUTO-GENERATED"
    new_block = "# New generated code"
    
    result = upsert_block_between_markers(original, start_marker, end_marker, new_block)
    
    assert start_marker in result
    assert end_marker in result
    assert new_block in result
    assert "# Existing content" in result
    
    # Should be at end
    lines = result.split("\n")
    begin_idx = lines.index(start_marker)
    end_idx = lines.index(end_marker)
    assert begin_idx > 0  # Not at start
    assert begin_idx < end_idx


def test_upsert_block_when_markers_exist():
    """Test that content between existing markers is replaced."""
    original = """# File header
router = APIRouter()

# BEGIN AUTO-GENERATED
# Old generated code
old_import()
# END AUTO-GENERATED

# File footer
"""
    start_marker = "# BEGIN AUTO-GENERATED"
    end_marker = "# END AUTO-GENERATED"
    new_block = "# New generated code\nnew_import()"
    
    result = upsert_block_between_markers(original, start_marker, end_marker, new_block)
    
    assert start_marker in result
    assert end_marker in result
    assert new_block in result
    assert "# New generated code" in result
    
    # Old content should be gone
    assert "Old generated code" not in result
    assert "old_import" not in result
    
    # Header and footer should remain
    assert "# File header" in result
    assert "# File footer" in result


def test_upsert_block_idempotent():
    """Test that running twice produces same result."""
    original = "# Content\n"
    start_marker = "# BEGIN TEST"
    end_marker = "# END TEST"
    new_block = "test_code()"
    
    result1 = upsert_block_between_markers(original, start_marker, end_marker, new_block)
    result2 = upsert_block_between_markers(result1, start_marker, end_marker, new_block)
    
    assert result1 == result2


def test_upsert_block_with_multiline():
    """Test that multiline blocks work correctly."""
    original = "# Header\n"
    start_marker = "# BEGIN MULTI"
    end_marker = "# END MULTI"
    new_block = "line1()\nline2()\nline3()"
    
    result = upsert_block_between_markers(original, start_marker, end_marker, new_block)
    
    assert "line1()" in result
    assert "line2()" in result
    assert "line3()" in result


def test_generate_router_block():
    """Test router block generation."""
    block = generate_router_block("stripe", "create_checkout_session")
    
    # Check new format uses proper module path
    assert "integrations.flows" in block
    assert "router.include_router" in block
    assert "/integrations/stripe/create_checkout_session" in block
    assert "stripe" in block


def test_generate_router_block_with_custom_module():
    """Test router block generation with custom module path."""
    block = generate_router_block(
        "stripe",
        "create_checkout_session",
        flows_module="src.integrations.flows",
        flow_module_name="stripe_create_checkout_session",
    )
    
    assert "from src.integrations.flows.stripe_create_checkout_session import" in block
    assert "router.include_router" in block
    assert "/integrations/stripe/create_checkout_session" in block


def test_generate_settings_block_with_url():
    """Test settings block generation with explicit URL."""
    block = generate_settings_block("stripe", "https://api.stripe.com")
    
    assert 'INTEGRATIONS["stripe"]' in block
    assert "https://api.stripe.com" in block
    assert "timeout_s=30" in block
    assert "retries=3" in block


def test_generate_settings_block_default_url():
    """Test settings block generation with default URL."""
    block = generate_settings_block("github")
    
    assert 'INTEGRATIONS["github"]' in block
    assert "https://api.github.com" in block


def test_marker_insertion_preserves_formatting():
    """Test that marker insertion doesn't break file formatting."""
    original = """from fastapi import APIRouter

router = APIRouter()

# Some existing routes
@router.get("/health")
def health():
    return {"status": "ok"}
"""
    
    start_marker = "# BEGIN AUTO-GENERATED ROUTES"
    end_marker = "# END AUTO-GENERATED ROUTES"
    new_block = 'router.include_router(test.router, prefix="/test")'
    
    result = upsert_block_between_markers(original, start_marker, end_marker, new_block)
    
    # Original routes should still be present
    assert "@router.get" in result
    assert "def health" in result
    
    # New block should be added
    assert "test.router" in result
    assert start_marker in result
    assert end_marker in result
