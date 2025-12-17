"""
Path utilities for spec-driven code generation.

Derives paths and import statements based on RepoProfile layout_hints
rather than hardcoded assumptions.
"""
import json
import re
from typing import Any, Dict, Optional, Tuple

from integration_coworker.graph.state import WorkflowState
from integration_coworker.repo.models import RepoProfile


def get_openapi_spec_dict(state: WorkflowState) -> Optional[Dict[str, Any]]:
    """
    Safely get openapi_spec as a dict from workflow state.
    
    Handles cases where openapi_spec might be:
    - None
    - A dict (normal case)
    - A JSON string (needs parsing)
    
    Args:
        state: WorkflowState containing the spec
        
    Returns:
        Parsed dict or None if unavailable/invalid
    """
    spec = state.openapi_spec
    if spec is None:
        return None
    
    if isinstance(spec, dict):
        return spec
    
    if isinstance(spec, str):
        try:
            parsed = json.loads(spec)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    
    return None


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
    
    Combines integrations_root with subdirectory layout_hints to produce
    full relative paths from repo root.
    
    Bug #34 fix: Handle case where layout_hints already contain full paths
    (e.g., "src/integrations/clients" instead of just "clients").
    
    Returns:
        Tuple of (clients_dir, flows_dir, tests_dir)
    """
    if not repo_profile:
        # Sensible defaults when no profile
        return (
            "src/integrations/clients",
            "src/integrations/flows",
            "tests/integrations",
        )

    # Get the base roots
    integrations_root = getattr(repo_profile, 'integrations_root', 'src/integrations')
    tests_root = getattr(repo_profile, 'tests_root', 'tests/integrations')
    
    hints = repo_profile.layout_hints or {}

    # Note: profile might use "workflows_dir" or "flows_dir"
    # Subdirectory names within integrations_root
    clients_subdir = hints.get("clients_dir", "clients")
    flows_subdir = hints.get("workflows_dir", hints.get("flows_dir", "flows"))
    
    # Strip trailing slashes for clean join
    integrations_root = integrations_root.rstrip("/")
    tests_root = tests_root.rstrip("/")
    
    # Bug #34 fix: Check if layout_hints already contain full paths
    # If clients_subdir already starts with integrations_root, use it directly
    # This happens with archetype detection which stores full paths in hints
    if clients_subdir.startswith(integrations_root):
        clients_dir = clients_subdir
    elif "/" in clients_subdir and not clients_subdir.startswith(integrations_root):
        # It's a full path but different root - use as-is
        clients_dir = clients_subdir
    else:
        # It's just a subdirectory name - combine with root
        clients_dir = f"{integrations_root}/{clients_subdir}"
    
    if flows_subdir.startswith(integrations_root):
        flows_dir = flows_subdir
    elif "/" in flows_subdir and not flows_subdir.startswith(integrations_root):
        flows_dir = flows_subdir
    else:
        flows_dir = f"{integrations_root}/{flows_subdir}"
    
    tests_dir = tests_root

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
    spec = get_openapi_spec_dict(state)
    if spec:
        servers = spec.get("servers", [])
        if servers and isinstance(servers, list) and len(servers) > 0:
            first_server = servers[0]
            if isinstance(first_server, dict) and "url" in first_server:
                url = first_server["url"]
                # Handle relative URLs (some specs use relative server paths)
                if url.startswith("/"):
                    return f"https://api.example.com{url}"
                return url

    # Strategy 3: Try to construct from spec info
    if spec:
        info = spec.get("info", {})
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
