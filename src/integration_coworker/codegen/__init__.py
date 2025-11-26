"""
Code generation utilities for the Integration Co-Worker.

This package contains helpers for spec-driven code generation,
including naming derivation, path computation, and LLM prompt building.
"""
from integration_coworker.codegen.naming import (
    derive_method_name,
    derive_client_class_name,
    derive_flow_function_name,
    derive_flow_module_name,
    derive_test_module_name,
    derive_client_module_name,
    to_snake_case,
    to_pascal_case,
)
from integration_coworker.codegen.paths import (
    path_to_module,
    derive_base_url,
    get_layout_dirs,
    compute_import_path,
)
from integration_coworker.codegen.prompts import (
    build_codegen_prompt,
)

__all__ = [
    "derive_method_name",
    "derive_client_class_name",
    "derive_flow_function_name",
    "derive_flow_module_name",
    "derive_test_module_name",
    "derive_client_module_name",
    "to_snake_case",
    "to_pascal_case",
    "path_to_module",
    "derive_base_url",
    "get_layout_dirs",
    "compute_import_path",
    "build_codegen_prompt",
]
