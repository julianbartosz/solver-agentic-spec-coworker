"""
Validation module for file integration.

Provides schema-driven validation code generation from FileSpec + FileField
+ FileValidationRule definitions.

Note: This module re-exports from codegen.validation_codegen for backward
compatibility. New code should import directly from integration_coworker.codegen.
"""

from integration_coworker.codegen.validation_codegen import (
    ValidationResult,
    generate_validator,
    generate_file_validator,
    generate_python_code,
    generate_pydantic_model,
)


__all__ = [
    "ValidationResult",
    "generate_validator",
    "generate_file_validator",
    "generate_python_code",
    "generate_pydantic_model",
]
