"""
Path utilities for spec-driven code generation.

Derives paths and import statements based on RepoProfile layout_hints
rather than hardcoded assumptions.
"""
import re
from typing import Dict, Any, Optional, Tuple

from integration_coworker.graph.state import WorkflowState
from integration_coworker.repo.models import RepoProfile


def path_to_module(path: str) -> str:
    """
    Convert a file path to a Python module path.
    
    Examples:
        "src/integrations/clients/mock_payments.py" -> "src.integrations.clients.mock_payments"
        "integrations/clients/stripe.py" -> "integrations.clients.stripe"
        "tests/integrations/test_foo.py" -> "tests.integrations.test_foo"
    """
    if not path:
        return ""
    
    # Normalize separators
    path = path.replace("\\", "/")
    
    # Remove .py extension
    if path.endswith(".py"):
        path = path[:-3]
    
    # Replace slashes with dots
    module = path.replace("/", ".")
    
    # Remove leading/trailing dots
    module = module.strip(".")
    
    return module


def strip_src_prefix(path: str) -> str:
    """
    Strip leading 'src/' from a path if present.
    
    This is useful for computing import paths since Python typically
    doesn't include 'src' in the module path when PYTHONPATH=src.
    
    Examples:
        "src/integrations/clients" -> "integrations/clients"
        "integrations/clients" -> "integrations/clients"
    """
    if path.startswith("src/"):
        return path[4:]
    return path


def get_layout_dirs(repo_profile: Optional[RepoProfile]) -> Tuple[str, str, str]:
    """
    Get the directory paths for clients, flows, and tests from a RepoProfile.
    
    Returns:
        Tuple of (clients_dir, flows_dir, tests_dir)
    """
    if not repo_profile or not repo_profile.layout_hints:
        # Sensible defaults
        return (
            "src/integrations/clients",
            "src/integrations/flows",
            "tests/integrations",
        )
    
    hints = repo_profile.layout_hints
    
    # Note: profile might use "workflows_dir" or "flows_dir"
    clients_dir = hints.get("clients_dir", "src/integrations/clients")
    flows_dir = hints.get("workflows_dir", hints.get("flows_dir", "src/integrations/flows"))
    tests_dir = hints.get("tests_dir", "tests/integrations")
    
    return (clients_dir, flows_dir, tests_dir)


def compute_import_path(
    from_file_dir: str,
    target_file_path: str,
    use_relative: bool = False,
) -> str:
    """
    Compute the import path from one module to another.
    
    Args:
        from_file_dir: Directory of the importing file (e.g., "src/integrations/flows")
        target_file_path: Full path of the target file (e.g., "src/integrations/clients/stripe.py")
        use_relative: If True, compute relative import; if False, compute absolute import
    
    Returns:
        Import module path (e.g., "integrations.clients.stripe" or "..clients.stripe")
    """
    target_module = path_to_module(target_file_path)
    
    if not use_relative:
        # For absolute imports, strip src/ prefix since PYTHONPATH usually includes src
        return path_to_module(strip_src_prefix(target_file_path))
    
    # For relative imports, compute relative path
    from_parts = strip_src_prefix(from_file_dir).split("/")
    target_parts = path_to_module(strip_src_prefix(target_file_path)).split(".")
    
    # Find common prefix
    common_len = 0
    for i, (a, b) in enumerate(zip(from_parts, target_parts)):
        if a == b:
            common_len = i + 1
        else:
            break
    
    # Calculate dots needed to go up from from_dir
    ups = len(from_parts) - common_len
    relative_prefix = "." * (ups + 1) if ups > 0 else "."
    
    # Add remaining target path
    remaining = target_parts[common_len:]
    if remaining:
        return relative_prefix + ".".join(remaining)
    return relative_prefix


def derive_base_url(state: WorkflowState) -> str:
    """
    Derive the API base URL from state, using multiple fallback strategies.
    
    Strategy:
    1. If state.source_system.base_url exists, use it
    2. Else, check OpenAPI spec's servers array
    3. Else, fall back to a neutral placeholder
    
    Args:
        state: WorkflowState containing spec and source system info
    
    Returns:
        Base URL string
    """
    # Strategy 1: Use source_system.base_url if available
    if state.source_system and state.source_system.base_url:
        return state.source_system.base_url
    
    # Strategy 2: Check OpenAPI spec servers
    if state.openapi_spec:
        servers = state.openapi_spec.get("servers", [])
        if servers and isinstance(servers, list) and len(servers) > 0:
            first_server = servers[0]
            if isinstance(first_server, dict) and "url" in first_server:
                url = first_server["url"]
                # Handle relative URLs (some specs use relative server paths)
                if url.startswith("/"):
                    return f"https://api.example.com{url}"
                return url
    
    # Strategy 3: Try to construct from spec info
    if state.openapi_spec:
        info = state.openapi_spec.get("info", {})
        title = info.get("title", "")
        if title:
            # Create a reasonable placeholder based on API name
            slug = re.sub(r'[^a-zA-Z0-9]+', '', title.lower())
            return f"https://api.{slug}.com"
    
    # Strategy 4: Use provider code
    if state.provider_code:
        provider = re.sub(r'[^a-zA-Z0-9]+', '', state.provider_code.lower())
        return f"https://api.{provider}.com"
    
    # Final fallback
    return "https://api.example.com"
