"""
Helper utilities for repo integration.

Provides marker-based block insertion for router and settings files
per Appendix G of the design spec.
"""
import re
from typing import Optional


def _find_smart_insertion_point(content: str) -> int:
    """
    Find the best insertion point for auto-generated code in a Python file.
    
    V41-001: Smart insertion to avoid placing code after `if __name__ == "__main__":`
    
    The insertion point is chosen in this priority order:
    1. Before `if __name__ == "__main__":` block (most common case)
    2. Before the last function/class definition (if no __main__ block)
    3. At the end of the file (fallback)
    
    This ensures auto-generated routes are importable when the module is
    imported by uvicorn or other ASGI servers.
    
    Args:
        content: The file content to analyze
        
    Returns:
        Character index where the new block should be inserted
    """
    lines = content.split('\n')
    
    # Strategy 1: Find `if __name__ == "__main__":` or variations
    main_patterns = [
        r'^if\s+__name__\s*==\s*["\']__main__["\']\s*:',
        r'^if\s+__name__\s*==\s*"__main__"\s*:',
        r"^if\s+__name__\s*==\s*'__main__'\s*:",
    ]
    
    main_line_idx = None
    for i, line in enumerate(lines):
        for pattern in main_patterns:
            if re.match(pattern, line.strip()):
                main_line_idx = i
                break
        if main_line_idx is not None:
            break
    
    if main_line_idx is not None:
        # Insert before the if __name__ block, with a blank line
        # Find the character position at the start of this line
        char_pos = sum(len(line) + 1 for line in lines[:main_line_idx])
        return char_pos
    
    # Strategy 2: Find the last top-level function or class before any standalone code
    # This handles files without if __name__ blocks
    last_def_end = None
    in_block = False
    block_indent = 0
    
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        current_indent = len(line) - len(stripped) if stripped else 0
        
        if stripped.startswith(('def ', 'class ', 'async def ')):
            in_block = True
            block_indent = current_indent
        elif in_block and stripped and current_indent <= block_indent and not stripped.startswith(('@', '#')):
            # End of block
            in_block = False
            last_def_end = i
    
    if last_def_end is not None and last_def_end < len(lines) - 1:
        # Insert after the last function/class
        char_pos = sum(len(line) + 1 for line in lines[:last_def_end])
        return char_pos
    
    # Strategy 3: Fallback - insert at end
    return len(content)


def upsert_block_between_markers(
    original: str,
    start_marker: str,
    end_marker: str,
    new_block: str,
) -> str:
    """
    Insert or replace content between markers in a file.
    
    Per Appendix G: Idempotent insertion of auto-generated blocks.
    
    V41-001: Smart insertion to place new blocks BEFORE `if __name__ == "__main__":`
    rather than at the end of the file. This ensures auto-generated routes
    are always importable.
    
    Args:
        original: Original file content
        start_marker: Beginning marker (e.g., "# BEGIN AUTO-GENERATED INTEGRATION ROUTES")
        end_marker: Ending marker (e.g., "# END AUTO-GENERATED INTEGRATION ROUTES")
        new_block: Content to place between markers
    
    Returns:
        Updated file content with new block between markers
        
    Behavior:
    - If markers exist: replace content between them
    - If not: insert before `if __name__` block (or at end if none)
    """
    # Check if markers already exist
    pattern = rf"({re.escape(start_marker)}.*?{re.escape(end_marker)})"
    match = re.search(pattern, original, re.DOTALL)

    if match:
        # Replace existing block
        replacement = f"{start_marker}\n{new_block}\n{end_marker}"
        return re.sub(pattern, replacement, original, flags=re.DOTALL)
    else:
        # V41-001: Smart insertion - find the best place to insert
        insertion_point = _find_smart_insertion_point(original)
        
        # Build the block to insert
        block_content = f"\n{start_marker}\n{new_block}\n{end_marker}\n"
        
        # Ensure there's proper spacing
        before = original[:insertion_point].rstrip('\n')
        after = original[insertion_point:].lstrip('\n')
        
        # Add appropriate newlines for clean formatting
        if before and not before.endswith('\n\n'):
            before += '\n\n'
        
        result = before + block_content
        if after:
            result += '\n' + after
        
        return result


def generate_router_block(
    provider_code: str,
    integration_slug: str,
    flows_module: str = "integrations.flows",
    flow_module_name: Optional[str] = None,
    target_file_type: str = "router",
) -> str:
    """
    Generate router registration block for FastAPI.
    
    Per Appendix G.3: Deterministic router insertion.
    
    V36-001 Fix: Uses full import paths to ensure imports resolve correctly.
    V40-001 Fix: Creates proper FastAPI routes that call flow functions instead
                 of incorrectly trying to access .router attribute on flow functions.
    V41-002 Fix: Handles different target file types (router file vs main.py):
                 - For router files: use existing `router` variable
                 - For main.py/app.py: use `app` or create new router
    
    DESIGN: Flow modules contain flow FUNCTIONS (e.g., `note_summarization_flow()`),
    not router objects. This function generates route handlers that call the flows.
    
    Args:
        provider_code: Provider code (e.g., "stripe", "openai")
        integration_slug: Integration task slug (e.g., "summarize", "create_checkout_session")
        flows_module: Module path for flows (e.g., "integrations.flows")
                      V36-001: Must be full path from package root
        flow_module_name: Specific flow module name (e.g., "openai_summarize")
        target_file_type: "router" for dedicated router files, "main" for app entry points.
                          V41-002: Controls which router object to use.
    
    Returns:
        Router registration code block with proper route definitions
    """
    # Use flow_module_name if provided, otherwise derive from provider + slug
    module_to_import = flow_module_name or f"{provider_code}_{integration_slug}"
    
    # V41-003 Fix: Normalize flows_module to use 'flows' consistently
    # The actual files are always created in 'flows/' directory, not 'workflows/'
    # This handles cases where the config file specifies 'workflows' as flows_dir
    if flows_module:
        # Step 1: Convert path separators to module separators for consistent handling
        flows_module = flows_module.replace('/', '.')
        
        # Step 2: Strip src. prefix if present (Python imports don't include it)
        if flows_module.startswith('src.'):
            flows_module = flows_module[4:]
        
        # Step 3: Replace 'workflows' with 'flows' in all positions
        flows_module = flows_module.replace('.workflows.', '.flows.')
        if flows_module.endswith('.workflows'):
            flows_module = flows_module[:-10] + '.flows'
        # Handle standalone "workflows" that will become the module name
        if flows_module == 'workflows':
            flows_module = 'flows'
    
    # V36-001 Fix: Ensure flows_module is a full path, not just "flows"
    # Must run AFTER normalization so we don't duplicate prefixes
    if flows_module and not flows_module.startswith("integrations") and "." not in flows_module:
        flows_module = f"integrations.{flows_module}"
    
    full_module = f"{flows_module}.{module_to_import}"
    
    # V40-001: Flow functions are named {task_slug}_flow
    flow_function_name = f"{integration_slug}_flow"
    
    # Convert slug to safe route path and function name
    route_path = integration_slug.replace("_", "-")
    handler_name = f"handle_{provider_code}_{integration_slug}"
    
    # V41-002: Determine the router object to use based on target file type
    if target_file_type == "main":
        # For main.py/app.py - create an inline router
        router_var = f"_{provider_code}_router"
        router_preamble = [
            "from fastapi import APIRouter",
            "",
            f"# Auto-generated integration router for {provider_code}",
            f'{router_var} = APIRouter(prefix="/{provider_code}", tags=["{provider_code}"])',
            "",
        ]
    else:
        # For dedicated router files - assume `router` exists
        router_var = "router"
        router_preamble = []

    # V40-001 Fix: Generate proper route handlers that call the flow function.
    # Uses asyncio.iscoroutinefunction() at runtime to handle both sync and async flows.
    # V42-005 Fix: Extract api_key from request or environment to match flow signature.
    # Standard flow signature is: flow_function(api_key: str, payload: Dict[str, Any], **kwargs)
    lines = router_preamble + [
        "import asyncio",
        "import os",
        f"from {full_module} import {flow_function_name}",
        "",
        f"@{router_var}.post('/{provider_code}/{route_path}')",
        f"async def {handler_name}(request: dict = None):",
        f'    """',
        f'    Auto-generated route for {provider_code} {integration_slug}.',
        f'    Delegates to the {flow_function_name} function.',
        f'    """',
        f"    # V42-005: Extract standard parameters from request",
        f"    # Flow signature is typically: flow(api_key, payload, **kwargs)",
        f"    payload = request or {{}}",
        f"    ",
        f"    # Extract api_key from request body, query param, or environment",
        f"    api_key = payload.pop('api_key', None) or os.environ.get('{provider_code.upper()}_API_KEY', '')",
        f"    ",
        f"    # V40-001: Handle both sync and async flow functions",
        f"    try:",
        f"        if asyncio.iscoroutinefunction({flow_function_name}):",
        f"            result = await {flow_function_name}(api_key=api_key, payload=payload)",
        f"        else:",
        f"            result = {flow_function_name}(api_key=api_key, payload=payload)",
        f"        return result",
        f"    except Exception as e:",
        f"        # Return structured error response",
        f"        return {{'error': str(e), 'success': False}}",
    ]
    
    # V41-002: For main.py targets, add the include_router call
    if target_file_type == "main":
        lines.extend([
            "",
            f"# Register the {provider_code} integration router",
            f"app.include_router({router_var})",
        ])
    
    return "\n".join(lines)


def generate_settings_block(provider_code: str, base_url: Optional[str] = None) -> str:
    """
    Generate provider settings block.
    
    Per Appendix G.4: Deterministic settings insertion.
    
    Uses dict literal instead of ProviderSettings class to avoid requiring
    additional imports in target files. The target repo can define its own
    ProviderSettings type if needed.
    
    Args:
        provider_code: Provider code
        base_url: API base URL (optional)
    
    Returns:
        Settings configuration code block
    """
    if base_url is None:
        base_url = f"https://api.{provider_code}.com"

    # Use dict literal for portability - no dependency on ProviderSettings class
    lines = [
        f'INTEGRATIONS["{provider_code}"] = {{',
        f'    "api_base_url": "{base_url}",',
        '    "timeout_s": 30,',
        '    "retries": 3,',
        "}",
    ]
    return "\n".join(lines)


def generate_app_router_registration_block(
    router_file: str,
    provider_code: str,
    integration_slug: str,
) -> str:
    """
    Generate app.include_router() block for main.py/app.py.
    
    BUG-009 FIX: System generated router_file but didn't update main.py
    to include the router with `app.include_router(...)`.
    
    V40-001: Properly handles src/ prefix stripping for Python import paths,
    since most projects set PYTHONPATH=src or equivalent.
    
    This generates the import and include_router statement that should be
    added to the main FastAPI application entry point.
    
    Args:
        router_file: Path to the integration router file (e.g., "src/routers/integrations.py")
        provider_code: Provider code (e.g., "openai", "stripe")
        integration_slug: Integration task slug (e.g., "summarize")
    
    Returns:
        Code block to import and register the integration router
        
    Example output:
        from routers.integrations import router as summarize_integrations_router
        
        app.include_router(
            summarize_integrations_router,
            prefix="/integrations/openai",
            tags=["integrations", "openai"],
        )
    """
    # Convert router_file path to module import path
    # e.g., "src/routers/integrations.py" -> "routers.integrations"
    module_path = router_file.replace("/", ".").replace("\\", ".")
    if module_path.endswith(".py"):
        module_path = module_path[:-3]
    
    # V40-001: Strip src. prefix - Python imports typically don't include it
    # when PYTHONPATH=src (standard convention)
    if module_path.startswith("src."):
        module_path = module_path[4:]
    
    # Generate a safe router alias that includes provider for uniqueness
    # e.g., "openai_summarize_router"
    router_alias = f"{provider_code}_{integration_slug}_router"
    
    lines = [
        f"from {module_path} import router as {router_alias}",
        "",
        "app.include_router(",
        f"    {router_alias},",
        f'    prefix="/integrations/{provider_code}",',
        f'    tags=["integrations", "{provider_code}"],',
        ")",
    ]
    return "\n".join(lines)
