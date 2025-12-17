"""
Schema-driven validation code generation.

This module generates Python validation functions from FileSpec + FileField
+ FileValidationRule definitions. The generated code can be used to:

1. Validate individual records (rows) against field rules
2. Validate entire files against file-level rules
3. Generate Pydantic models for type-safe validation

Supported rule types:
- REQUIRED: Field must be non-empty
- LENGTH: Field length must be within min/max bounds
- RANGE: Numeric field must be within value bounds
- REGEX: Field must match regex pattern
- ENUM: Field must be one of allowed values
- FORMAT: Field must match date/number format
- UNIQUE: Field values must be unique across file
- LOOKUP: Field value must exist in reference set
- CROSS_FIELD: Complex multi-field validation

Generated validators are pure Python functions with no external dependencies
beyond the standard library, ensuring portability.

Per docs/FILE_INTEGRATION_V1_PLAN.md Section 6.1
"""

import re
import textwrap
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from integration_coworker.domain.models import (
    FileField,
    FileSpec,
    FileValidationRule,
    ValidationRuleType,
)


@dataclass
class ValidationResult:
    """
    Result of validating a record or field.
    
    Attributes:
        is_valid: True if all validations passed
        errors: List of error messages (severity=error)
        warnings: List of warning messages (severity=warning)
        field_errors: Dict mapping field name to list of errors
    """
    is_valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    field_errors: Dict[str, List[str]] = field(default_factory=dict)
    
    def add_error(self, message: str, field_name: Optional[str] = None) -> None:
        """Add an error message."""
        self.is_valid = False
        self.errors.append(message)
        if field_name:
            if field_name not in self.field_errors:
                self.field_errors[field_name] = []
            self.field_errors[field_name].append(message)
    
    def add_warning(self, message: str) -> None:
        """Add a warning message."""
        self.warnings.append(message)
    
    def merge(self, other: "ValidationResult") -> None:
        """Merge another result into this one."""
        if not other.is_valid:
            self.is_valid = False
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        for field_name, errors in other.field_errors.items():
            if field_name not in self.field_errors:
                self.field_errors[field_name] = []
            self.field_errors[field_name].extend(errors)


def generate_validator(
    file_spec: FileSpec,
    fields: List[FileField],
    rules: List[FileValidationRule],
) -> Callable[[Dict[str, Any]], ValidationResult]:
    """
    Generate a validation function for a FileSpec.
    
    The returned function takes a record (dict of field_name -> value)
    and returns a ValidationResult.
    
    Args:
        file_spec: The file specification
        fields: List of field definitions
        rules: List of validation rules
        
    Returns:
        A callable that validates records
    """
    # Build lookup maps
    field_map = {f.name: f for f in fields}
    field_rules: Dict[str, List[FileValidationRule]] = {}
    file_level_rules: List[FileValidationRule] = []
    
    for rule in rules:
        if rule.field_name:
            if rule.field_name not in field_rules:
                field_rules[rule.field_name] = []
            field_rules[rule.field_name].append(rule)
        else:
            file_level_rules.append(rule)
    
    # Build individual field validators
    field_validators: Dict[str, List[Callable[[Any], Tuple[bool, str]]]] = {}
    
    for field_name, rules_list in field_rules.items():
        field_validators[field_name] = []
        for rule in rules_list:
            validator = _create_field_validator(rule, field_map.get(field_name))
            if validator:
                field_validators[field_name].append(validator)
    
    def validate_record(record: Dict[str, Any]) -> ValidationResult:
        """Validate a single record."""
        result = ValidationResult()
        
        # Validate each field
        for field_name, validators in field_validators.items():
            value = record.get(field_name)
            
            for validator in validators:
                is_valid, message = validator(value)
                if not is_valid:
                    result.add_error(message, field_name)
        
        # Apply file-level rules that can work on single records
        for rule in file_level_rules:
            if rule.rule_type == ValidationRuleType.CROSS_FIELD:
                is_valid, message = _validate_cross_field(rule, record)
                if not is_valid:
                    result.add_error(message)
        
        return result
    
    return validate_record


def generate_file_validator(
    file_spec: FileSpec,
    fields: List[FileField],
    rules: List[FileValidationRule],
) -> Callable[[List[Dict[str, Any]]], ValidationResult]:
    """
    Generate a file-level validation function.
    
    The returned function takes all records and validates them,
    including rules that require full-file context (e.g., UNIQUE).
    
    Args:
        file_spec: The file specification
        fields: List of field definitions
        rules: List of validation rules
        
    Returns:
        A callable that validates entire files
    """
    record_validator = generate_validator(file_spec, fields, rules)
    
    # Find unique rules
    unique_rules = [
        r for r in rules 
        if r.rule_type == ValidationRuleType.UNIQUE and r.field_name
    ]
    
    def validate_file(records: List[Dict[str, Any]]) -> ValidationResult:
        """Validate all records in a file."""
        result = ValidationResult()
        
        # Track unique values
        unique_sets: Dict[str, Set[Any]] = {r.field_name: set() for r in unique_rules}
        unique_duplicates: Dict[str, Set[Any]] = {r.field_name: set() for r in unique_rules}
        
        for i, record in enumerate(records):
            # Validate record
            record_result = record_validator(record)
            
            # Prefix errors with row number
            for error in record_result.errors:
                result.add_error(f"Row {i + 1}: {error}")
            for warning in record_result.warnings:
                result.add_warning(f"Row {i + 1}: {warning}")
            
            # Track unique violations
            for rule in unique_rules:
                field_name = rule.field_name
                value = record.get(field_name)
                if value is not None and value != "":
                    if value in unique_sets[field_name]:
                        unique_duplicates[field_name].add(value)
                    else:
                        unique_sets[field_name].add(value)
        
        # Report unique violations
        for rule in unique_rules:
            field_name = rule.field_name
            if unique_duplicates[field_name]:
                dups = list(unique_duplicates[field_name])[:5]
                error_msg = rule.error_message or f"Duplicate values in {field_name}: {dups}"
                result.add_error(error_msg, field_name)
        
        return result
    
    return validate_file


def generate_python_code(
    file_spec: FileSpec,
    fields: List[FileField],
    rules: List[FileValidationRule],
    function_name: str = "validate_record",
) -> str:
    """
    Generate Python source code for a validation function.
    
    The generated code is self-contained and can be saved to a file
    or executed with exec().
    
    Args:
        file_spec: The file specification
        fields: List of field definitions
        rules: List of validation rules
        function_name: Name for the generated function
        
    Returns:
        Python source code as a string
    """
    lines = [
        '"""',
        f'Auto-generated validation code for {file_spec.name}',
        f'File type: {file_spec.file_type}',
        '"""',
        '',
        'import re',
        'from typing import Any, Dict, List, Tuple',
        '',
        '',
        'class ValidationResult:',
        '    """Result of validation."""',
        '    def __init__(self):',
        '        self.is_valid = True',
        '        self.errors: List[str] = []',
        '        self.warnings: List[str] = []',
        '        self.field_errors: Dict[str, List[str]] = {}',
        '',
        '    def add_error(self, message: str, field_name: str = None) -> None:',
        '        self.is_valid = False',
        '        self.errors.append(message)',
        '        if field_name:',
        '            if field_name not in self.field_errors:',
        '                self.field_errors[field_name] = []',
        '            self.field_errors[field_name].append(message)',
        '',
        '    def add_warning(self, message: str) -> None:',
        '        self.warnings.append(message)',
        '',
        '',
    ]
    
    # Generate field validators
    for i, rule in enumerate(rules):
        if rule.field_name:
            validator_code = _generate_validator_code(rule, i)
            lines.extend(validator_code.split('\n'))
            lines.append('')
    
    # Generate main validator function
    lines.extend([
        f'def {function_name}(record: Dict[str, Any]) -> ValidationResult:',
        f'    """Validate a {file_spec.name} record."""',
        '    result = ValidationResult()',
        '',
    ])
    
    # Add calls to field validators
    for i, rule in enumerate(rules):
        if rule.field_name:
            lines.extend([
                f'    # Rule: {rule.rule_type} for {rule.field_name}',
                f'    value = record.get("{rule.field_name}")',
                f'    is_valid, msg = _validate_rule_{i}(value)',
                f'    if not is_valid:',
                f'        result.add_error(msg, "{rule.field_name}")',
                '',
            ])
    
    lines.extend([
        '    return result',
        '',
    ])
    
    return '\n'.join(lines)


def generate_pydantic_model(
    file_spec: FileSpec,
    fields: List[FileField],
    rules: List[FileValidationRule],
    model_name: Optional[str] = None,
) -> str:
    """
    Generate Pydantic model code for type-safe validation.
    
    Args:
        file_spec: The file specification
        fields: List of field definitions
        rules: List of validation rules
        model_name: Name for the generated model (defaults to spec name)
        
    Returns:
        Python source code defining a Pydantic model
    """
    if model_name is None:
        # Convert file spec name to PascalCase
        model_name = ''.join(word.title() for word in file_spec.name.split('_')) + 'Record'
    
    lines = [
        '"""',
        f'Auto-generated Pydantic model for {file_spec.name}',
        '"""',
        '',
        'from typing import Optional',
        'from pydantic import BaseModel, Field, field_validator',
        'from decimal import Decimal',
        'from datetime import date, datetime',
        '',
        '',
        f'class {model_name}(BaseModel):',
        f'    """Pydantic model for {file_spec.name} records."""',
        '',
    ]
    
    # Add fields
    for f in fields:
        field_type = _python_type_for_field(f)
        default = 'None' if f.nullable else '...'
        
        lines.append(f'    {f.name}: {field_type} = {default}')
    
    lines.append('')
    
    # Add validators
    for rule in rules:
        if rule.field_name and rule.rule_type in (ValidationRuleType.REGEX, ValidationRuleType.ENUM):
            validator_code = _generate_pydantic_validator(rule)
            lines.extend(validator_code.split('\n'))
    
    return '\n'.join(lines)


# ============================================================================
# Internal Functions
# ============================================================================

def _create_field_validator(
    rule: FileValidationRule,
    field_def: Optional[FileField],
) -> Optional[Callable[[Any], Tuple[bool, str]]]:
    """Create a validator function for a rule."""
    rule_type = rule.rule_type
    config = rule.rule_config or {}
    error_msg = rule.error_message
    
    if rule_type == ValidationRuleType.REQUIRED:
        def validate_required(value: Any) -> Tuple[bool, str]:
            if value is None or value == "" or (isinstance(value, str) and value.strip() == ""):
                return False, error_msg or f"{rule.field_name} is required"
            return True, ""
        return validate_required
    
    elif rule_type == ValidationRuleType.LENGTH:
        min_len = config.get("min", 0)
        max_len = config.get("max")
        
        def validate_length(value: Any) -> Tuple[bool, str]:
            if value is None or value == "":
                return True, ""  # Length check doesn't apply to empty
            str_val = str(value)
            if len(str_val) < min_len:
                return False, error_msg or f"{rule.field_name} must be at least {min_len} characters"
            if max_len and len(str_val) > max_len:
                return False, error_msg or f"{rule.field_name} must be at most {max_len} characters"
            return True, ""
        return validate_length
    
    elif rule_type == ValidationRuleType.RANGE:
        min_val = config.get("min")
        max_val = config.get("max")
        
        def validate_range(value: Any) -> Tuple[bool, str]:
            if value is None or value == "":
                return True, ""
            try:
                num_val = float(value)
                if min_val is not None and num_val < min_val:
                    return False, error_msg or f"{rule.field_name} must be >= {min_val}"
                if max_val is not None and num_val > max_val:
                    return False, error_msg or f"{rule.field_name} must be <= {max_val}"
                return True, ""
            except (ValueError, TypeError):
                return False, error_msg or f"{rule.field_name} must be a number"
        return validate_range
    
    elif rule_type == ValidationRuleType.REGEX:
        pattern = config.get("pattern", ".*")
        try:
            compiled = re.compile(pattern)
        except re.error:
            return None
        
        def validate_regex(value: Any) -> Tuple[bool, str]:
            if value is None or value == "":
                return True, ""
            if not compiled.match(str(value)):
                return False, error_msg or f"{rule.field_name} must match pattern {pattern}"
            return True, ""
        return validate_regex
    
    elif rule_type == ValidationRuleType.ENUM:
        allowed = set(config.get("values", []))
        
        def validate_enum(value: Any) -> Tuple[bool, str]:
            if value is None or value == "":
                return True, ""
            if str(value) not in allowed:
                return False, error_msg or f"{rule.field_name} must be one of {list(allowed)}"
            return True, ""
        return validate_enum
    
    elif rule_type == ValidationRuleType.FORMAT:
        fmt = config.get("format", "")
        
        def validate_format(value: Any) -> Tuple[bool, str]:
            if value is None or value == "":
                return True, ""
            # Try to parse as date if format looks like date
            if "Y" in fmt or "d" in fmt or "m" in fmt:
                try:
                    from datetime import datetime
                    datetime.strptime(str(value), fmt.replace("YYYY", "%Y").replace("MM", "%m").replace("DD", "%d"))
                    return True, ""
                except ValueError:
                    return False, error_msg or f"{rule.field_name} must match format {fmt}"
            return True, ""
        return validate_format
    
    return None


def _validate_cross_field(rule: FileValidationRule, record: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate a cross-field rule using safe expression evaluation.
    
    Uses simpleeval instead of eval() to prevent arbitrary code execution.
    See docs/BUCKET_2_TECH_DEBT_AND_SCALING.md TD-SEC-001 for details.
    """
    from integration_coworker.codegen.safe_eval import (
        safe_eval_cross_field,
        CrossFieldEvaluationError,
    )
    
    config = rule.rule_config or {}
    expression = config.get("expression", "True")
    error_msg = rule.error_message or "Cross-field validation failed"
    
    try:
        result = safe_eval_cross_field(expression, record)
        return (result, "") if result else (False, error_msg)
    except CrossFieldEvaluationError as e:
        return False, f"Expression error: {e}"


def _generate_validator_code(rule: FileValidationRule, index: int) -> str:
    """Generate Python code for a validator function."""
    config = rule.rule_config or {}
    error_msg = rule.error_message or f"Validation failed for {rule.field_name}"
    
    if rule.rule_type == ValidationRuleType.REQUIRED:
        return f'''
def _validate_rule_{index}(value) -> Tuple[bool, str]:
    if value is None or value == "" or (isinstance(value, str) and value.strip() == ""):
        return False, "{error_msg}"
    return True, ""
'''
    
    elif rule.rule_type == ValidationRuleType.LENGTH:
        min_len = config.get("min", 0)
        max_len = config.get("max")
        
        # Generate conditional max_len check to avoid "is not None" with literal
        if max_len is not None:
            max_check = f'''
    if len(str_val) > {max_len}:
        return False, "{error_msg}"'''
        else:
            max_check = ""
        
        return f'''
def _validate_rule_{index}(value) -> Tuple[bool, str]:
    if value is None or value == "":
        return True, ""
    str_val = str(value)
    if len(str_val) < {min_len}:
        return False, "{error_msg}"{max_check}
    return True, ""
'''
    
    elif rule.rule_type == ValidationRuleType.REGEX:
        pattern = config.get("pattern", ".*")
        return f'''
_PATTERN_{index} = re.compile(r"{pattern}")
def _validate_rule_{index}(value) -> Tuple[bool, str]:
    if value is None or value == "":
        return True, ""
    if not _PATTERN_{index}.match(str(value)):
        return False, "{error_msg}"
    return True, ""
'''
    
    elif rule.rule_type == ValidationRuleType.ENUM:
        values = config.get("values", [])
        return f'''
_ALLOWED_{index} = {set(values)}
def _validate_rule_{index}(value) -> Tuple[bool, str]:
    if value is None or value == "":
        return True, ""
    if str(value) not in _ALLOWED_{index}:
        return False, "{error_msg}"
    return True, ""
'''
    
    return f'''
def _validate_rule_{index}(value) -> Tuple[bool, str]:
    return True, ""  # Not implemented: {rule.rule_type}
'''


def _generate_pydantic_validator(rule: FileValidationRule) -> str:
    """Generate Pydantic field validator code."""
    config = rule.rule_config or {}
    
    if rule.rule_type == ValidationRuleType.ENUM:
        values = config.get("values", [])
        values_repr = repr(set(values))
        values_str = str(values)  # For error message
        return f'''
    @field_validator('{rule.field_name}')
    @classmethod
    def validate_{rule.field_name}(cls, v):
        _allowed = {values_repr}
        if v is not None and v not in _allowed:
            raise ValueError("{rule.field_name} must be one of " + str(list(_allowed)))
        return v
'''
    
    elif rule.rule_type == ValidationRuleType.REGEX:
        pattern = config.get("pattern", ".*")
        # Escape backslashes for the error message (non-raw string context)
        escaped_pattern = pattern.replace("\\", "\\\\")
        return f'''
    @field_validator('{rule.field_name}')
    @classmethod
    def validate_{rule.field_name}(cls, v):
        if v is not None and not re.match(r'{pattern}', str(v)):
            raise ValueError('{rule.field_name} must match pattern {escaped_pattern}')
        return v
'''
    
    return ""


def _python_type_for_field(field: FileField) -> str:
    """Map FileFieldType to Python type annotation."""
    type_map = {
        "string": "str",
        "integer": "int",
        "decimal": "Decimal",
        "date": "date",
        "datetime": "datetime",
        "boolean": "bool",
        "email": "str",
        "uuid": "str",
        "json": "dict",
        "binary": "bytes",
    }
    base_type = type_map.get(field.field_type, "str")
    
    if field.nullable:
        return f"Optional[{base_type}]"
    return base_type


__all__ = [
    "ValidationResult",
    "generate_validator",
    "generate_file_validator",
    "generate_python_code",
    "generate_pydantic_model",
]
