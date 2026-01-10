"""
Post-Generation Validator - V35 Bug Prevention

This module provides a comprehensive validation layer that runs AFTER code generation
but BEFORE files are written to the target repository.

It integrates:
- import_fixer.py (V35-001): Fix hallucinated imports
- task_parser.py (V35-002): Validate task path requirements
- repo_type_detector.py (V35-003): Validate repo type assumptions
- coordinated_artifacts.py (V35-004/005/006): Validate import consistency

Usage:
------
    from integration_coworker.codegen.post_generation_validator import (
        validate_and_fix_generated_code,
        ValidationResult,
    )
    
    result = validate_and_fix_generated_code(
        generated_code=code,
        artifact_type="flow",
        repo_root="/path/to/repo",
        task_description="Implement foo in bar.py",
    )
    
    if result.has_critical_errors:
        raise ValueError(f"Generation failed: {result.errors}")
    
    # Use fixed code
    fixed_code = result.fixed_code
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ValidationIssue:
    """A single validation issue."""
    category: str       # "import", "path", "repo_type", "consistency"
    severity: str       # "error", "warning", "info"
    message: str
    line_number: Optional[int] = None
    original_code: Optional[str] = None
    fixed_code: Optional[str] = None
    auto_fixed: bool = False


@dataclass
class ValidationResult:
    """Result of post-generation validation."""
    original_code: str
    fixed_code: str
    issues: List[ValidationIssue] = field(default_factory=list)
    
    @property
    def has_errors(self) -> bool:
        """Check if there are any error-level issues."""
        return any(i.severity == "error" for i in self.issues)
    
    @property
    def has_critical_errors(self) -> bool:
        """Check if there are unfixed critical errors."""
        return any(
            i.severity == "error" and not i.auto_fixed 
            for i in self.issues
        )
    
    @property
    def error_count(self) -> int:
        """Count of error-level issues."""
        return sum(1 for i in self.issues if i.severity == "error")
    
    @property
    def warning_count(self) -> int:
        """Count of warning-level issues."""
        return sum(1 for i in self.issues if i.severity == "warning")
    
    @property
    def was_modified(self) -> bool:
        """Check if the code was modified."""
        return self.original_code != self.fixed_code
    
    def get_summary(self) -> str:
        """Get a human-readable summary."""
        parts = []
        
        if self.was_modified:
            parts.append("Code was auto-fixed")
        
        if self.error_count:
            parts.append(f"{self.error_count} errors")
        
        if self.warning_count:
            parts.append(f"{self.warning_count} warnings")
        
        if not parts:
            return "Validation passed"
        
        return ", ".join(parts)


def _validate_imports(code: str, artifact_type: str) -> Tuple[str, List[ValidationIssue]]:
    """
    Validate and fix imports using import_fixer.
    
    V35-001: Fix hallucinated import paths
    """
    issues: List[ValidationIssue] = []
    fixed_code = code
    
    try:
        from integration_coworker.codegen.import_fixer import (
            fix_imports_in_code,
            validate_runtime_imports,
            ImportValidationResult,
        )
        
        # Validate imports
        validation = validate_runtime_imports(code)
        
        for warning in validation.warnings:
            issues.append(ValidationIssue(
                category="import",
                severity="warning",
                message=warning,
            ))
        
        for unresolved in validation.unresolved_imports:
            issues.append(ValidationIssue(
                category="import",
                severity="error",
                message=unresolved,
            ))
        
        # Fix imports - returns (fixed_code, list_of_fixes)
        fixed_code, fixes = fix_imports_in_code(code)
        
        # Check if fixes were applied
        if fixes:
            issues.append(ValidationIssue(
                category="import",
                severity="info",
                message=f"Auto-fixed {len(fixes)} hallucinated imports",
                auto_fixed=True,
            ))
            logger.info(f"[V35-001] Auto-fixed {len(fixes)} hallucinated imports")
        
    except ImportError:
        logger.warning("import_fixer not available, skipping import validation")
    except Exception as e:
        logger.error(f"Import validation error: {e}")
        issues.append(ValidationIssue(
            category="import",
            severity="error",
            message=f"Import validation failed: {e}",
        ))
    
    return fixed_code, issues


def _validate_task_paths(
    code: str,
    artifact_type: str,
    task_description: Optional[str],
    expected_path: Optional[str],
) -> List[ValidationIssue]:
    """
    Validate that generated code matches task path requirements.
    
    V35-002: Ensure task-specified paths are honored
    """
    issues: List[ValidationIssue] = []
    
    if not task_description:
        return issues
    
    try:
        from integration_coworker.codegen.task_parser import (
            extract_task_requirements,
            should_override_template_path,
        )
        
        requirements = extract_task_requirements(task_description)
        
        # Check if task specifies explicit paths
        if requirements.explicit_file_paths:
            for path in requirements.explicit_file_paths:
                if expected_path and path not in expected_path:
                    issues.append(ValidationIssue(
                        category="path",
                        severity="warning",
                        message=f"Task specifies path '{path}' but generating to '{expected_path}'",
                    ))
        
        # Check if template path should be overridden
        # V38-002 Fix: should_override_template_path takes TaskPathExtractionResult, not (str, str)
        if expected_path and requirements.primary_target_path:
            should_override = should_override_template_path(requirements)
            
            if should_override and requirements.primary_target_path != expected_path:
                issues.append(ValidationIssue(
                    category="path",
                    severity="warning",
                    message=f"Task path override suggested: task specifies '{requirements.primary_target_path}' but generating to '{expected_path}'",
                ))
        
    except ImportError:
        logger.warning("task_parser not available, skipping path validation")
    except Exception as e:
        logger.error(f"Task path validation error: {e}")
    
    return issues


def _validate_repo_type(
    code: str,
    artifact_type: str,
    repo_root: Optional[str],
) -> List[ValidationIssue]:
    """
    Validate repo type assumptions in generated code.
    
    V35-003: Ensure we don't assume FastAPI for CLI tools
    """
    issues: List[ValidationIssue] = []
    
    if not repo_root:
        return issues
    
    try:
        from integration_coworker.codegen.repo_type_detector import (
            detect_repo_type,
            should_generate_fastapi_router,
            RepoType,
        )
        
        detection = detect_repo_type(repo_root)
        
        # Check for FastAPI usage in non-web repos
        if detection.repo_type == RepoType.CLI_TOOL:
            if "FastAPI" in code or "APIRouter" in code:
                issues.append(ValidationIssue(
                    category="repo_type",
                    severity="error",
                    message=f"Generated FastAPI code for CLI tool repo (detected as {detection.repo_type.value})",
                ))
            
            if "@app.route" in code or "@router." in code:
                issues.append(ValidationIssue(
                    category="repo_type",
                    severity="error",
                    message="Generated web routes for CLI tool repo",
                ))
        
        # Log detection for debugging
        logger.debug(
            f"[V35-003] Repo type: {detection.repo_type.value}, "
            f"confidence: {detection.confidence}"
        )
        
    except ImportError:
        logger.warning("repo_type_detector not available, skipping repo type validation")
    except Exception as e:
        logger.error(f"Repo type validation error: {e}")
    
    return issues


def _validate_cross_artifact_consistency(
    code: str,
    artifact_type: str,
    provider_code: Optional[str],
    task_slug: Optional[str],
) -> List[ValidationIssue]:
    """
    Validate that artifact imports are consistent.
    
    V35-004/005/006: Ensure import paths match file locations
    """
    issues: List[ValidationIssue] = []
    
    if artifact_type not in ("flow", "test"):
        return issues
    
    if not provider_code or not task_slug:
        return issues
    
    try:
        # Check for common import mismatches
        if artifact_type == "flow":
            # Flow should import from integrations.clients.X
            if "from integrations.clients" in code:
                # Check consistency
                expected_module = f"integrations.clients.{provider_code}"
                if expected_module not in code:
                    issues.append(ValidationIssue(
                        category="consistency",
                        severity="warning",
                        message=f"Flow may have inconsistent client import (expected {expected_module})",
                    ))
        
        elif artifact_type == "test":
            # Test should import from integrations.flows.X
            if "from integrations.flows" in code:
                expected_module = f"integrations.flows.{provider_code}_{task_slug}"
                if expected_module not in code:
                    issues.append(ValidationIssue(
                        category="consistency",
                        severity="warning",
                        message=f"Test may have inconsistent flow import (expected {expected_module})",
                    ))
        
    except Exception as e:
        logger.error(f"Consistency validation error: {e}")
    
    return issues


def _validate_python_syntax(code: str) -> List[ValidationIssue]:
    """
    Validate Python syntax.
    """
    issues: List[ValidationIssue] = []
    
    try:
        import ast
        ast.parse(code)
    except SyntaxError as e:
        issues.append(ValidationIssue(
            category="syntax",
            severity="error",
            message=f"Python syntax error: {e.msg}",
            line_number=e.lineno,
        ))
    
    return issues


def validate_and_fix_generated_code(
    generated_code: str,
    artifact_type: str = "unknown",
    repo_root: Optional[str] = None,
    task_description: Optional[str] = None,
    expected_path: Optional[str] = None,
    provider_code: Optional[str] = None,
    task_slug: Optional[str] = None,
    fix_imports: bool = True,
    validate_syntax: bool = True,
) -> ValidationResult:
    """
    Validate and fix generated code before writing to repo.
    
    This is the main entry point for post-generation validation.
    
    Args:
        generated_code: The generated code to validate
        artifact_type: Type of artifact ("client", "flow", "test")
        repo_root: Path to target repository
        task_description: Original task description
        expected_path: Expected file path for this artifact
        provider_code: API provider code (e.g., "openai")
        task_slug: Task slug (e.g., "create_completion")
        fix_imports: Whether to auto-fix import issues
        validate_syntax: Whether to validate Python syntax
        
    Returns:
        ValidationResult with fixed code and issues
    """
    all_issues: List[ValidationIssue] = []
    fixed_code = generated_code
    
    logger.info(f"[PostGen] Validating {artifact_type} artifact")
    
    # 1. Fix and validate imports (V35-001)
    if fix_imports:
        fixed_code, import_issues = _validate_imports(fixed_code, artifact_type)
        all_issues.extend(import_issues)
    
    # 2. Validate task paths (V35-002)
    path_issues = _validate_task_paths(
        fixed_code, artifact_type, task_description, expected_path
    )
    all_issues.extend(path_issues)
    
    # 3. Validate repo type (V35-003)
    repo_issues = _validate_repo_type(fixed_code, artifact_type, repo_root)
    all_issues.extend(repo_issues)
    
    # 4. Validate cross-artifact consistency (V35-004/005/006)
    consistency_issues = _validate_cross_artifact_consistency(
        fixed_code, artifact_type, provider_code, task_slug
    )
    all_issues.extend(consistency_issues)
    
    # 5. Validate Python syntax
    if validate_syntax and artifact_type != "router":
        syntax_issues = _validate_python_syntax(fixed_code)
        all_issues.extend(syntax_issues)
    
    result = ValidationResult(
        original_code=generated_code,
        fixed_code=fixed_code,
        issues=all_issues,
    )
    
    logger.info(f"[PostGen] Validation complete: {result.get_summary()}")
    
    return result


def validate_artifact_set(
    client_code: Optional[str],
    flow_code: Optional[str],
    test_code: Optional[str],
    repo_root: str,
    provider_code: str,
    task_slug: str,
    task_description: Optional[str] = None,
) -> Dict[str, ValidationResult]:
    """
    Validate a complete set of artifacts together.
    
    This ensures coordinated validation across all artifacts.
    
    Args:
        client_code: Generated client code
        flow_code: Generated flow code
        test_code: Generated test code
        repo_root: Path to target repository
        provider_code: API provider code
        task_slug: Task slug
        task_description: Original task description
        
    Returns:
        Dict mapping artifact type to ValidationResult
    """
    results = {}
    
    if client_code:
        results["client"] = validate_and_fix_generated_code(
            generated_code=client_code,
            artifact_type="client",
            repo_root=repo_root,
            task_description=task_description,
            expected_path=f"integrations/clients/{provider_code}.py",
            provider_code=provider_code,
            task_slug=task_slug,
        )
    
    if flow_code:
        results["flow"] = validate_and_fix_generated_code(
            generated_code=flow_code,
            artifact_type="flow",
            repo_root=repo_root,
            task_description=task_description,
            expected_path=f"integrations/flows/{provider_code}_{task_slug}.py",
            provider_code=provider_code,
            task_slug=task_slug,
        )
    
    if test_code:
        results["test"] = validate_and_fix_generated_code(
            generated_code=test_code,
            artifact_type="test",
            repo_root=repo_root,
            task_description=task_description,
            expected_path=f"tests/test_{provider_code}_{task_slug}.py",
            provider_code=provider_code,
            task_slug=task_slug,
        )
    
    return results
