"""
Helper utilities for repo integration.

Provides marker-based block insertion for router and settings files
per Appendix G of the design spec.
"""
import re
from typing import Optional


def upsert_block_between_markers(
    original: str,
    start_marker: str,
    end_marker: str,
    new_block: str,
) -> str:
    """
    Insert or replace content between markers in a file.
    
    Per Appendix G: Idempotent insertion of auto-generated blocks.
    
    Args:
        original: Original file content
        start_marker: Beginning marker (e.g., "# BEGIN AUTO-GENERATED INTEGRATION ROUTES")
        end_marker: Ending marker (e.g., "# END AUTO-GENERATED INTEGRATION ROUTES")
        new_block: Content to place between markers
    
    Returns:
        Updated file content with new block between markers
        
    Behavior:
    - If markers exist: replace content between them
    - If not: append markers + block at end of file
    """
    # Check if markers already exist
    pattern = rf"({re.escape(start_marker)}.*?{re.escape(end_marker)})"
    match = re.search(pattern, original, re.DOTALL)
    
    if match:
        # Replace existing block
        replacement = f"{start_marker}\n{new_block}\n{end_marker}"
        return re.sub(pattern, replacement, original, flags=re.DOTALL)
    else:
        # Append new block at end
        if not original.endswith("\n"):
            original += "\n"
        return f"{original}\n{start_marker}\n{new_block}\n{end_marker}\n"


def generate_router_block(
    provider_code: str,
    integration_slug: str,
    flows_module: str = "integrations.flows",
    flow_module_name: str = None,
) -> str:
    """
    Generate router registration block for FastAPI.
    
    Per Appendix G.3: Deterministic router insertion.
    
    Args:
        provider_code: Provider code (e.g., "stripe", "mock_payments")
        integration_slug: Integration task slug
        flows_module: Module path for flows (e.g., "integrations.flows")
        flow_module_name: Specific flow module name (e.g., "mock_payments_create_checkout_session")
    
    Returns:
        Router registration code block
    """
    # Use flow_module_name if provided, otherwise use integration_slug
    module_to_import = flow_module_name or integration_slug
    full_module = f"{flows_module}.{module_to_import}"
    
    lines = [
        f"from {full_module} import {integration_slug}_flow as flow_module",
        "",
        "router.include_router(",
        f"    flow_module.router if hasattr(flow_module, 'router') else APIRouter(),",
        f'    prefix="/integrations/{provider_code}/{integration_slug}",',
        f'    tags=["{provider_code}"],',
        ")",
    ]
    return "\n".join(lines)


def generate_settings_block(provider_code: str, base_url: Optional[str] = None) -> str:
    """
    Generate provider settings block.
    
    Per Appendix G.4: Deterministic settings insertion.
    
    Args:
        provider_code: Provider code
        base_url: API base URL (optional)
    
    Returns:
        Settings configuration code block
    """
    if base_url is None:
        base_url = f"https://api.{provider_code}.com"
    
    lines = [
        f'INTEGRATIONS["{provider_code}"] = ProviderSettings(',
        f'    api_base_url="{base_url}",',
        "    timeout_s=30,",
        "    retries=3,",
        ")",
    ]
    return "\n".join(lines)
