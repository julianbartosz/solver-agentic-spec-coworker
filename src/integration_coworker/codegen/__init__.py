"""
Code generation utilities for the Integration Co-Worker.

This package contains helpers for spec-driven code generation,
including naming derivation, path computation, and LLM prompt building.
"""
from integration_coworker.codegen.context import CodegenContext
from integration_coworker.codegen.naming import (
    derive_method_name,
    derive_client_class_name,
    derive_flow_function_name,
    derive_flow_module_name,
    derive_test_module_name,
    derive_test_class_name,
    derive_client_module_name,
    build_codegen_context,
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
from integration_coworker.codegen.policy_templates import (
    PolicyCodeSnippet,
    get_auth_template,
    get_retry_template,
    get_rate_limit_template,
    get_logging_template,
    get_idempotency_template,
    build_policy_code,
    inject_policies_into_client_code,
)
from integration_coworker.codegen.config_templates import (
    ServiceConfig,
    EnvVariable,
    generate_docker_compose,
    generate_env_template,
    generate_settings_yaml,
    generate_gitignore_additions,
)
from integration_coworker.codegen.field_mappings import (
    FieldMapping,
    RequestMapping,
    ResponseMapping,
    generate_request_mapping,
    generate_response_mapping,
    generate_field_mappings,
    to_snake_case,
    to_camel_case,
    merge_mappings,
)
from integration_coworker.codegen.validation_codegen import (
    ValidationResult,
    generate_validator,
    generate_file_validator,
    generate_python_code,
    generate_pydantic_model,
)
from integration_coworker.codegen.semantic_validator import (
    SemanticIssue,
    validate_semantic_correctness,
    validate_imports,
    validate_class_signature,
    validate_function_exists,
    format_semantic_issues,
)
# V35 Bug Fix Modules
from integration_coworker.codegen.import_fixer import (
    fix_imports_in_code,
    fix_missing_stdlib_imports,  # V45-002
    validate_runtime_imports,
    fix_generated_code_imports,
    ImportValidationResult,
)
from integration_coworker.codegen.task_parser import (
    extract_task_requirements,
    should_override_template_path,
    get_target_file_path,
    TaskPathExtractionResult,
)
from integration_coworker.codegen.repo_type_detector import (
    detect_repo_type,
    should_generate_fastapi_router,
    get_integration_template_type,
    RepoType,
    IntegrationStrategy,
    RepoTypeDetectionResult,
)
from integration_coworker.codegen.coordinated_artifacts import (
    CoordinatedArtifactSet,
    ArtifactLocation,
    create_coordinated_artifact_set,
    validate_artifact_imports,
    fix_artifact_imports,
    write_coordinated_artifacts,
    verify_written_artifacts,
)
from integration_coworker.codegen.post_generation_validator import (
    validate_and_fix_generated_code,
    validate_artifact_set,
    ValidationResult as PostGenValidationResult,
    ValidationIssue,
)

__all__ = [
    # Context (single source of truth)
    "CodegenContext",
    "build_codegen_context",
    # Naming
    "derive_method_name",
    "derive_client_class_name",
    "derive_flow_function_name",
    "derive_flow_module_name",
    "derive_test_module_name",
    "derive_test_class_name",
    "derive_client_module_name",
    "to_snake_case",
    "to_pascal_case",
    "path_to_module",
    "derive_base_url",
    "get_layout_dirs",
    "compute_import_path",
    "build_codegen_prompt",
    # Policy templates
    "PolicyCodeSnippet",
    "get_auth_template",
    "get_retry_template",
    "get_rate_limit_template",
    "get_logging_template",
    "get_idempotency_template",
    "build_policy_code",
    "inject_policies_into_client_code",
    # Config templates
    "ServiceConfig",
    "EnvVariable",
    "generate_docker_compose",
    "generate_env_template",
    "generate_settings_yaml",
    "generate_gitignore_additions",
    # Field mappings
    "FieldMapping",
    "RequestMapping",
    "ResponseMapping",
    "generate_request_mapping",
    "generate_response_mapping",
    "generate_field_mappings",
    "to_camel_case",
    "merge_mappings",
    # Validation codegen
    "ValidationResult",
    "generate_validator",
    "generate_file_validator",
    "generate_python_code",
    "generate_pydantic_model",
    # Semantic validation (code quality)
    "SemanticIssue",
    "validate_semantic_correctness",
    "validate_imports",
    "validate_class_signature",
    "validate_function_exists",
    "format_semantic_issues",
    # V35 Bug Fix Modules
    # Import fixer (V35-001)
    "fix_imports_in_code",
    "fix_missing_stdlib_imports",  # V45-002
    "validate_runtime_imports",
    "fix_generated_code_imports",
    "ImportValidationResult",
    # Task parser (V35-002)
    "extract_task_requirements",
    "should_override_template_path",
    "get_target_file_path",
    "TaskPathExtractionResult",
    # Repo type detector (V35-003)
    "detect_repo_type",
    "should_generate_fastapi_router",
    "get_integration_template_type",
    "RepoType",
    "IntegrationStrategy",
    "RepoTypeDetectionResult",
    # Coordinated artifacts (V35-004/005/006)
    "CoordinatedArtifactSet",
    "ArtifactLocation",
    "create_coordinated_artifact_set",
    "validate_artifact_imports",
    "fix_artifact_imports",
    "write_coordinated_artifacts",
    "verify_written_artifacts",
    # Post-generation validator
    "validate_and_fix_generated_code",
    "validate_artifact_set",
    "PostGenValidationResult",
    "ValidationIssue",
]
