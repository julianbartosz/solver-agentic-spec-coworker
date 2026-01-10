"""
CodegenContext - Single source of truth for all derived names in code generation.

This module prevents naming misalignment bugs by computing all names once
and passing them as a unified context to all artifact generators.

Design rationale (ADR-CODEGEN-001):
- Client methods are derived from endpoint operation_id
- Flow functions are derived from task_slug
- Test mocks must match client methods exactly
- By bundling all names in one dataclass, it's impossible to use wrong name

V36-003: Enhanced to support task-extracted paths for explicit user requirements.
When the user specifies "create file at src/myapp/ai_enhancer.py", we honor that.

V39-007: Enhanced to detect existing API clients in target repositories.
When a compatible client exists, we reuse it instead of generating a duplicate.
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Tuple

from integration_coworker.domain.models import Endpoint


# Language-to-extension mapping (must stay in sync with prompts.LANGUAGE_CONVENTIONS)
LANGUAGE_EXTENSIONS: Dict[str, str] = {
    "python": ".py",
    "typescript": ".ts",
    "javascript": ".js",
    "go": ".go",
    "java": ".java",
    "ruby": ".rb",
    "csharp": ".cs",
}


def get_file_extension(language: str) -> str:
    """Get file extension for a language, with fallback."""
    lang_key = language.lower().strip()
    # Handle common aliases
    aliases = {"py": "python", "ts": "typescript", "js": "javascript", "golang": "go", "c#": "csharp", "cs": "csharp"}
    lang_key = aliases.get(lang_key, lang_key)
    return LANGUAGE_EXTENSIONS.get(lang_key, ".py")


@dataclass(frozen=True)
class CodegenContext:
    """
    All derived names for code generation - single source of truth.
    
    This dataclass is frozen (immutable) to ensure names cannot be
    accidentally modified after derivation.
    
    V36-003: Now supports task-extracted path overrides. When a user explicitly
    requests a specific file path in their task description, that path takes
    precedence over template-derived paths.
    
    Usage:
        ctx = build_codegen_context(state)
        client_code = _generate_client_code(ctx, state)
        flow_code = _generate_flow_code(ctx, state)
        test_code = _generate_test_code(ctx, state)
    
    All artifact generators receive the SAME context, guaranteeing
    that method_name, client_class, etc. are consistent across all.
    """
    
    # Provider/task identifiers
    provider_code: str
    task_slug: str
    
    # Client naming
    client_module: str      # e.g., "stainless"
    client_class: str       # e.g., "StainlessClient"
    method_name: str        # e.g., "create_assistant" (from endpoint.operation_id)
    client_import_path: str # e.g., "integrations.clients.stainless"
    
    # Flow naming
    flow_module: str        # e.g., "stainless_create_chat_completion_with_openai"
    flow_function: str      # e.g., "create_chat_completion_with_openai_flow"
    flow_import_path: str   # e.g., "integrations.flows.stainless_create_chat..."
    
    # Test naming
    test_module: str        # e.g., "test_stainless_create_chat_completion..."
    test_class: str         # e.g., "TestStainlessFlow"
    
    # Paths (from RepoProfile)
    clients_dir: str        # e.g., "src/integrations/clients"
    flows_dir: str          # e.g., "src/integrations/flows"
    tests_dir: str          # e.g., "tests/integrations"
    
    # Language for correct file extensions (Bug #1 fix)
    language: str = "python"  # e.g., "python", "typescript", "javascript"
    
    # API context
    base_url: str = ""      # e.g., "https://api.openai.com/v1"
    endpoint: Optional[Endpoint] = None  # Primary endpoint for code generation
    
    # V36-003: Task-extracted path overrides
    # These take precedence over template-derived paths when set
    task_explicit_path: Optional[str] = None  # User's explicit path request
    task_function_name: Optional[str] = None  # User's explicit function name
    task_class_name: Optional[str] = None     # User's explicit class name
    has_task_overrides: bool = False          # Quick check if overrides exist
    
    # V38-007: Async code generation flag
    is_async_required: bool = False           # True if task requires async/concurrent patterns
    
    # V38-008: Streaming response handling flag
    is_streaming_required: bool = False       # True if task requires streaming response handling
    
    # V39-007: Existing client detection
    # When an existing client is found in the target repo, skip generation and reuse
    existing_client_detected: bool = False    # True if target repo has a compatible client
    existing_client_class: Optional[str] = None      # Class name of existing client
    existing_client_import: Optional[str] = None     # Full import statement for existing client
    existing_client_module: Optional[str] = None     # Module path for existing client
    existing_client_methods: tuple = field(default_factory=tuple)  # Available methods
    skip_client_generation: bool = False      # True if we should skip client artifact generation
    
    def get_client_rel_path(self) -> str:
        """
        Get relative path for client artifact with correct language extension.
        
        V37-003: When flow has explicit path outside integrations/, place client
        in a `clients/` subdirectory near the flow for better organization.
        
        BUG-PATH-001 FIX: Uses join_path to prevent double-slashes.
        """
        from integration_coworker.codegen.paths import join_path
        
        ext = get_file_extension(self.language)
        
        # V37-003: Relocate client near explicit flow path for non-integrations flows
        if self.task_explicit_path:
            from pathlib import PurePath
            flow_path = PurePath(self.task_explicit_path)
            flow_dir = str(flow_path.parent)
            
            # Check if flow is outside the standard integrations directory
            if not flow_dir.startswith("integrations") and "integrations" not in flow_dir:
                # Place client in a clients/ subdirectory next to the flow
                # e.g., src/docformatter/ai_enhancer.py -> src/docformatter/clients/openai.py
                client_dir = join_path(flow_dir, "clients")
                return join_path(client_dir, f"{self.client_module}{ext}")
        
        return join_path(self.clients_dir, f"{self.client_module}{ext}")
    
    def get_flow_rel_path(self) -> str:
        """
        Get relative path for flow artifact.
        
        V36-003: Returns task-explicit path if user specified one.
        V39-005: Enhanced logging for path debugging.
        
        BUG-PATH-001 FIX: Uses join_path to prevent double-slashes.
        """
        import logging
        from integration_coworker.codegen.paths import join_path
        
        logger = logging.getLogger(__name__)
        
        # V36-003: Honor explicit user path if provided
        if self.task_explicit_path:
            logger.info(
                f"[V39-005] Using task-explicit flow path: {self.task_explicit_path} "
                f"(overriding default: {self.flows_dir}/{self.flow_module}.py)"
            )
            return self.task_explicit_path
        
        ext = get_file_extension(self.language)
        default_path = join_path(self.flows_dir, f"{self.flow_module}{ext}")
        logger.debug(f"[V39-005] Using default flow path: {default_path}")
        return default_path
    
    def get_test_rel_path(self) -> str:
        """
        Get relative path for test artifact.
        
        V36-003: Derives test path from task-explicit path if one was provided.
        
        BUG-PATH-001 FIX: Uses join_path to prevent double-slashes.
        """
        from integration_coworker.codegen.paths import join_path
        
        ext = get_file_extension(self.language)
        
        # V36-003: Derive test path from explicit flow path
        if self.task_explicit_path:
            from pathlib import PurePath
            explicit = PurePath(self.task_explicit_path)
            stem = explicit.stem
            # Put test alongside the explicit path or in tests dir
            if str(explicit).startswith("src/"):
                # src/myapp/ai_enhancer.py -> tests/test_ai_enhancer.py
                return join_path("tests", f"test_{stem}{ext}")
            else:
                # myapp/ai_enhancer.py -> tests/myapp/test_ai_enhancer.py
                parent = explicit.parent
                return join_path("tests", str(parent), f"test_{stem}{ext}")
        
        return join_path(self.tests_dir, f"{self.test_module}{ext}")
    
    def get_effective_flow_function(self) -> str:
        """
        Get the effective flow function name.
        
        V36-003: Returns task-extracted function name if available.
        """
        if self.task_function_name:
            return self.task_function_name
        return self.flow_function
    
    def get_effective_client_class(self) -> str:
        """
        Get the effective client class name.
        
        V36-003: Returns task-extracted class name if available.
        V39-007: Returns existing client class if one was detected in target repo.
        """
        # V39-007: Use existing client if detected
        if self.existing_client_detected and self.existing_client_class:
            return self.existing_client_class
        
        if self.task_class_name:
            return self.task_class_name
        return self.client_class
    
    def get_effective_client_import_statement(self) -> str:
        """
        Get the full import statement for the client.
        
        V39-007: Returns existing client import if one was detected in target repo.
        Otherwise, constructs the default import statement.
        
        Returns:
            Full import statement (e.g., "from clients.openai import OpenAIClient")
        """
        # V39-007: Use existing client import if detected
        if self.existing_client_detected and self.existing_client_import:
            return self.existing_client_import
        
        # Default: construct from module path and class name
        module_path = self.get_effective_client_import_path()
        class_name = self.get_effective_client_class()
        return f"from {module_path} import {class_name}"
    
    def get_effective_client_import_path(self) -> str:
        """
        Get the effective import path for the client from within the flow.
        
        V37-001 Fix: When the flow has a custom path (task_explicit_path), we need
        to compute the correct import path from the flow's location to the client.
        
        V39-003 Enhancement: Better validation and logging of computed paths
        to catch import/path mismatches early.
        
        V39-007 Enhancement: Use existing client module if detected in target repo.
        
        This ensures imports work correctly when:
        - Flow is at default location (integrations/flows/) -> use default import path
        - Flow is at custom location (src/myapp/ai_enhancer.py) -> compute correct path
        - Existing client detected -> use existing client's module path
        
        Returns:
            Import path for the client module (e.g., "integrations.clients.openai")
        """
        import logging
        logger = logging.getLogger(__name__)
        
        # V39-007: Use existing client module if detected
        if self.existing_client_detected and self.existing_client_module:
            logger.info(
                f"[V39-007] Using existing client module path: {self.existing_client_module} "
                f"(skipping generated client)"
            )
            return self.existing_client_module
        
        # If no custom flow path, use the default client import path
        if not self.task_explicit_path:
            return self.client_import_path
        
        # V37-001: Compute the correct import path for relocated flow
        from integration_coworker.codegen.paths import (
            compute_client_import_for_flow,
            should_use_relative_import,
            path_to_module,
            strip_src_prefix,
        )
        
        flow_path = self.task_explicit_path
        client_path = self.get_client_rel_path()
        
        # V39-003: Validate that client_path is consistent with flow_path
        # When flow is at backend/plugins_ai/summarize.py, client should be at
        # backend/plugins_ai/clients/openai.py, NOT backend/plugins/clients/openai.py
        if client_path:
            from pathlib import PurePath
            flow_parent = str(PurePath(flow_path).parent)
            
            # Check if client path respects the flow's location
            if flow_parent and flow_parent not in client_path:
                logger.warning(
                    f"[V39-003] Client path '{client_path}' doesn't include flow parent '{flow_parent}'. "
                    f"This may cause import errors. Consider relocating client."
                )
        
        # Determine if we should use relative imports
        use_relative = should_use_relative_import(flow_path, client_path)
        
        computed_import = compute_client_import_for_flow(
            flow_file_path=flow_path,
            client_file_path=client_path,
            prefer_relative=use_relative,
        )
        
        # V39-003: Validate that computed import matches actual file path
        # Convert client_path to expected module path for comparison
        expected_module = path_to_module(strip_src_prefix(client_path))
        
        # For relative imports, we can't directly compare, but for absolute we can
        if not computed_import.startswith('.') and computed_import != expected_module:
            logger.warning(
                f"[V39-003] Import mismatch detected: "
                f"computed='{computed_import}', expected='{expected_module}' from client_path='{client_path}'"
            )
            # Use the expected module path since it's derived from actual file path
            return expected_module
        
        logger.debug(
            f"[V39-003] Computed client import: '{computed_import}' "
            f"(flow={flow_path}, client={client_path}, relative={use_relative})"
        )
        
        return computed_import
