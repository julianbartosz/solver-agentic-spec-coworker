"""
Multi-Language Validation Gates Package

Provides language-specific validation gates for Tier 1 (production) and Tier 2 (experimental)
code generation validation.

Architecture:
- LanguageStrategy: ABC defining provision() and validate() phases
- GateRunner: Executes individual gates and returns structured results
- Registry: Maps language strings to strategy implementations

2-Phase Design (per MULTI_LANG_CODEGEN_PRODUCTION_PLAN.md):
1. Provision Phase (network allowed): npm ci, go mod download
2. Validate Phase (network disabled): tsc, eslint, vitest, go test, etc.
"""

from integration_coworker.codegen.gates.base import (
    LanguageStrategy,
    GateResult,
    GateStatus,
    ValidationResult,
    ProvisionError,
    GateError,
    ToolchainMissingError,
    Tier,
    SandboxEnv,
    ArtifactFile,
)
from integration_coworker.codegen.gates.registry import (
    GATE_REGISTRY,
    get_strategy,
    register_strategy,
    list_languages,
)

__all__ = [
    # Base classes
    "LanguageStrategy",
    "GateResult", 
    "GateStatus",
    "ValidationResult",
    "ProvisionError",
    "GateError",
    "ToolchainMissingError",
    "Tier",
    "SandboxEnv",
    "ArtifactFile",
    # Registry
    "GATE_REGISTRY",
    "get_strategy",
    "register_strategy",
    "list_languages",
]
