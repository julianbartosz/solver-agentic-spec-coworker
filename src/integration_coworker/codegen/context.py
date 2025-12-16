"""
CodegenContext - Single source of truth for all derived names in code generation.

This module prevents naming misalignment bugs by computing all names once
and passing them as a unified context to all artifact generators.

Design rationale (ADR-CODEGEN-001):
- Client methods are derived from endpoint operation_id
- Flow functions are derived from task_slug
- Test mocks must match client methods exactly
- By bundling all names in one dataclass, it's impossible to use wrong name
"""
from dataclasses import dataclass
from typing import Optional, Dict, Any

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
    
    def get_client_rel_path(self) -> str:
        """Get relative path for client artifact with correct language extension."""
        ext = get_file_extension(self.language)
        return f"{self.clients_dir}/{self.client_module}{ext}"
    
    def get_flow_rel_path(self) -> str:
        """Get relative path for flow artifact with correct language extension."""
        ext = get_file_extension(self.language)
        return f"{self.flows_dir}/{self.flow_module}{ext}"
    
    def get_test_rel_path(self) -> str:
        """Get relative path for test artifact with correct language extension."""
        ext = get_file_extension(self.language)
        return f"{self.tests_dir}/{self.test_module}{ext}"
