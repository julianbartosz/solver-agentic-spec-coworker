"""
Path utilities for spec-driven code generation.

Derives paths and import statements based on RepoProfile layout_hints
rather than hardcoded assumptions.
"""
import json
import os
import re
from typing import Any, Dict, Optional, Tuple

from integration_coworker.graph.state import WorkflowState
from integration_coworker.repo.models import RepoProfile


def normalize_path(path: str) -> str:
    """
    Normalize a file path to prevent double-slashes and other path issues.
    
    Fixes BUG-PATH-001: Double-slash in generated file paths.
    
    This function:
    - Removes consecutive slashes (// -> /)
    - Removes leading slashes for relative paths
    - Strips trailing slashes for directory paths
    - Handles empty path segments
    
    Args:
        path: File path to normalize
        
    Returns:
        Normalized path without double-slashes
        
    Examples:
        >>> normalize_path("backend//openai.py")
        'backend/openai.py'
        >>> normalize_path("flows//openai_create_note.py")
        'flows/openai_create_note.py'
        >>> normalize_path("/src//clients///stripe.py")
        'src/clients/stripe.py'
    """
    if not path:
        return path
    
    # Use os.path.normpath for platform-independent normalization
    # but preserve forward slashes for consistency
    normalized = os.path.normpath(path)
    
    # os.path.normpath converts to platform separators, convert back to /
    normalized = normalized.replace(os.sep, "/")
    
    # Remove leading slash for relative paths (unless it's an absolute path)
    if normalized.startswith("/") and not path.startswith("/"):
        normalized = normalized.lstrip("/")
    
    # Strip trailing slash
    normalized = normalized.rstrip("/")
    
    return normalized


def join_path(*parts: str) -> str:
    """
    Join path parts safely, preventing double-slashes.
    
    This is a safer alternative to f-string path construction like:
    f"{dir}/{filename}" which can produce "backend//file.py"
    
    Args:
        *parts: Path parts to join
        
    Returns:
        Normalized joined path
        
    Examples:
        >>> join_path("backend/", "openai.py")
        'backend/openai.py'
        >>> join_path("src/integrations", "clients", "stripe.py")
        'src/integrations/clients/stripe.py'
    """
    # Filter out empty parts
    non_empty_parts = [p for p in parts if p and p.strip()]
    if not non_empty_parts:
        return ""
    
    # Join with / and normalize
    joined = "/".join(non_empty_parts)
    return normalize_path(joined)


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
    
    V45-PATH-001: Consistent defaults - use RepoProfile.integrations_root default
    which is "integrations" (not "src/integrations"). This ensures path consistency
    between code generation and import generation.
    
    Returns:
        Tuple of (clients_dir, flows_dir, tests_dir)
    """
    # V45-PATH-001: Use RepoProfile-consistent defaults
    # RepoProfile.integrations_root defaults to "integrations"
    # RepoProfile.tests_root defaults to "tests"
    DEFAULT_INTEGRATIONS_ROOT = "integrations"
    DEFAULT_TESTS_ROOT = "tests"
    
    if not repo_profile:
        # Sensible defaults when no profile - MUST match RepoProfile defaults
        return (
            f"{DEFAULT_INTEGRATIONS_ROOT}/clients",
            f"{DEFAULT_INTEGRATIONS_ROOT}/flows",
            DEFAULT_TESTS_ROOT,
        )

    # Get the base roots from profile, falling back to RepoProfile defaults
    integrations_root = getattr(repo_profile, 'integrations_root', None) or DEFAULT_INTEGRATIONS_ROOT
    tests_root = getattr(repo_profile, 'tests_root', None) or DEFAULT_TESTS_ROOT
    
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

    # BUG-PATH-001 FIX: Normalize all paths to prevent double-slashes
    return (
        normalize_path(clients_dir),
        normalize_path(flows_dir),
        normalize_path(tests_dir),
    )


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
    1. If state.api_base_url exists (V38-002 preserved URL), use it
    2. If state.source_system.base_url exists, use it
    3. Else, check OpenAPI spec's servers array
    4. Else, fall back to a neutral placeholder
    
    Args:
        state: WorkflowState containing spec and source system info
    
    Returns:
        Base URL string
    """
    # Strategy 1: Use api_base_url if already extracted (V38-002)
    # This survives state_gc cleanup unlike openapi_spec
    if state.api_base_url:
        return state.api_base_url

    # Strategy 2: Use source_system.base_url if available
    if state.source_system and state.source_system.base_url:
        return state.source_system.base_url

    # Strategy 3: Check OpenAPI spec servers
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

    # Strategy 4: Try to construct from spec info
    if spec:
        info = spec.get("info", {})
        title = info.get("title", "")
        if title:
            # Create a reasonable placeholder based on API name
            slug = re.sub(r'[^a-zA-Z0-9]+', '', title.lower())
            return f"https://api.{slug}.com"

    # Strategy 5: Use provider code
    if state.provider_code:
        provider = re.sub(r'[^a-zA-Z0-9]+', '', state.provider_code.lower())
        return f"https://api.{provider}.com"

    # Final fallback
    return "https://api.example.com"


def compute_client_import_for_flow(
    flow_file_path: str,
    client_file_path: str,
    prefer_relative: bool = True,
) -> str:
    """
    Compute the correct import path for a client from within a flow file.
    
    V37-001 Fix: When a flow is relocated to a custom path (e.g., src/myapp/ai_enhancer.py),
    the client import needs to be adjusted accordingly:
    - If both are in the same package tree, use relative imports
    - If they're in different trees, compute the correct absolute path
    - Always consider PYTHONPATH=src convention
    
    Args:
        flow_file_path: Path to the flow file (e.g., "src/docformatter/ai_enhancer.py")
        client_file_path: Path to the client file (e.g., "src/integrations/clients/openai.py")
        prefer_relative: If True, prefer relative imports when possible
        
    Returns:
        Import statement path (e.g., "integrations.clients.openai" or "..integrations.clients.openai")
        
    Examples:
        # Flow and client in different trees (absolute import)
        compute_client_import_for_flow(
            "src/docformatter/ai_enhancer.py",
            "src/integrations/clients/openai.py",
            prefer_relative=False
        ) -> "integrations.clients.openai"
        
        # Relative import from custom flow location
        compute_client_import_for_flow(
            "src/myapp/flows/feature.py",
            "src/myapp/clients/api.py",
            prefer_relative=True
        ) -> "..clients.api"
    """
    from pathlib import PurePath
    
    # Normalize paths
    flow_path = PurePath(flow_file_path.replace("\\", "/"))
    client_path = PurePath(client_file_path.replace("\\", "/"))
    
    # Get directories
    flow_dir = flow_path.parent
    
    # Strip src/ for module path computation
    flow_dir_str = str(flow_dir)
    client_path_str = str(client_path)
    
    if prefer_relative:
        # Try to compute relative import
        rel_import = compute_import_path(
            from_file_dir=flow_dir_str,
            target_file_path=client_path_str,
            use_relative=True,
        )
        
        # Check if relative import is reasonable (not too many ups)
        if rel_import.count('.') <= 4:  # Max 3 levels up
            return rel_import
    
    # Fall back to absolute import (stripped of src/)
    return compute_import_path(
        from_file_dir=flow_dir_str,
        target_file_path=client_path_str,
        use_relative=False,
    )


def should_use_relative_import(flow_path: str, client_path: str) -> bool:
    """
    Determine if relative imports should be used based on package structure.
    
    V37-001: Use relative imports when:
    - Flow and client are in the same package tree
    - Flow is in a custom location within a recognized package
    
    Use absolute imports when:
    - Flow and client are in completely different trees
    - The paths suggest different Python packages
    
    Args:
        flow_path: Path to the flow file
        client_path: Path to the client file
        
    Returns:
        True if relative imports should be used
    """
    from pathlib import PurePath
    
    flow = PurePath(flow_path.replace("\\", "/"))
    client = PurePath(client_path.replace("\\", "/"))
    
    # Check if they share a common src-level ancestor
    flow_parts = list(flow.parts)
    client_parts = list(client.parts)
    
    # Skip 'src' if present
    if flow_parts and flow_parts[0] == "src":
        flow_parts = flow_parts[1:]
    if client_parts and client_parts[0] == "src":
        client_parts = client_parts[1:]
    
    # If the first real package is the same, use relative
    if flow_parts and client_parts and flow_parts[0] == client_parts[0]:
        return True
    
    # If flow is in a custom location (not integrations), absolute is safer
    if flow_parts and flow_parts[0] != "integrations":
        return False
    
    # Default to absolute for cross-package imports
    return False
