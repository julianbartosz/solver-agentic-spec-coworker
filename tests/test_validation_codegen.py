"""
Tests for validation code generation.

Tests cover:
1. ValidationResult behavior
2. Runtime validator generation
3. Python code generation
4. Pydantic model generation
5. All rule types (required, length, range, regex, enum, format, unique, cross_field)
"""

import pytest

pytestmark = pytest.mark.requires_simpleeval

from integration_coworker.domain.models import (
    FileField,
    FileSpec,
    FileValidationRule,
    FileType,
    ValidationRuleType,
)
from integration_coworker.codegen.validation_codegen import (
    ValidationResult,
    generate_validator,
    generate_file_validator,
    generate_python_code,
    generate_pydantic_model,
)


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def simple_file_spec():
    """A simple CSV file spec."""
    return FileSpec(
        id=1,
        source_system_id=1,
        name="customers",
        file_type=FileType.CSV.value,
        has_header=True,
    )


@pytest.fixture
def customer_fields():
    """Customer file fields."""
    return [
        FileField(id=1, file_spec_id=1, name="customer_id", field_type="integer", position=0, nullable=False),
        FileField(id=2, file_spec_id=1, name="name", field_type="string", position=1, nullable=False),
        FileField(id=3, file_spec_id=1, name="email", field_type="string", position=2, nullable=True),
        FileField(id=4, file_spec_id=1, name="age", field_type="integer", position=3, nullable=True),
        FileField(id=5, file_spec_id=1, name="status", field_type="string", position=4, nullable=False),
    ]


@pytest.fixture
def customer_rules():
    """Validation rules for customer file."""
    return [
        # Required fields
        FileValidationRule(
            id=1, file_spec_id=1, field_name="customer_id",
            rule_type=ValidationRuleType.REQUIRED,
            error_message="Customer ID is required"
        ),
        FileValidationRule(
            id=2, file_spec_id=1, field_name="name",
            rule_type=ValidationRuleType.REQUIRED,
            error_message="Name is required"
        ),
        # Length rule
        FileValidationRule(
            id=3, file_spec_id=1, field_name="name",
            rule_type=ValidationRuleType.LENGTH,
            rule_config={"min": 2, "max": 100},
            error_message="Name must be 2-100 characters"
        ),
        # Range rule
        FileValidationRule(
            id=4, file_spec_id=1, field_name="age",
            rule_type=ValidationRuleType.RANGE,
            rule_config={"min": 0, "max": 150},
            error_message="Age must be 0-150"
        ),
        # Regex rule
        FileValidationRule(
            id=5, file_spec_id=1, field_name="email",
            rule_type=ValidationRuleType.REGEX,
            rule_config={"pattern": r"^[\w\.\-]+@[\w\.\-]+\.\w+$"},
            error_message="Invalid email format"
        ),
        # Enum rule
        FileValidationRule(
            id=6, file_spec_id=1, field_name="status",
            rule_type=ValidationRuleType.ENUM,
            rule_config={"values": ["active", "inactive", "pending"]},
            error_message="Status must be active, inactive, or pending"
        ),
    ]


# ============================================================================
# ValidationResult Tests
# ============================================================================

class TestValidationResult:
    """Test ValidationResult behavior."""
    
    def test_initial_state_is_valid(self):
        """New result should be valid."""
        result = ValidationResult()
        assert result.is_valid
        assert len(result.errors) == 0
        assert len(result.warnings) == 0
    
    def test_add_error_marks_invalid(self):
        """Adding error should mark as invalid."""
        result = ValidationResult()
        result.add_error("Something wrong")
        assert not result.is_valid
        assert "Something wrong" in result.errors
    
    def test_add_error_with_field_name(self):
        """Error with field name should be tracked."""
        result = ValidationResult()
        result.add_error("Field X is bad", "field_x")
        assert "field_x" in result.field_errors
        assert "Field X is bad" in result.field_errors["field_x"]
    
    def test_add_warning_keeps_valid(self):
        """Adding warning should not mark as invalid."""
        result = ValidationResult()
        result.add_warning("Something to note")
        assert result.is_valid
        assert "Something to note" in result.warnings
    
    def test_merge_combines_results(self):
        """Merge should combine errors and warnings."""
        result1 = ValidationResult()
        result1.add_error("Error 1")
        
        result2 = ValidationResult()
        result2.add_warning("Warning 1")
        
        result1.merge(result2)
        assert "Error 1" in result1.errors
        assert "Warning 1" in result1.warnings
    
    def test_merge_invalid_propagates(self):
        """Merging invalid result should make target invalid."""
        result1 = ValidationResult()  # valid
        result2 = ValidationResult()
        result2.add_error("Error")  # invalid
        
        result1.merge(result2)
        assert not result1.is_valid


# ============================================================================
# Runtime Validator Tests
# ============================================================================

class TestGenerateValidator:
    """Test runtime validator generation."""
    
    def test_generates_callable(self, simple_file_spec, customer_fields, customer_rules):
        """Should generate a callable validator."""
        validator = generate_validator(simple_file_spec, customer_fields, customer_rules)
        assert callable(validator)
    
    def test_valid_record_passes(self, simple_file_spec, customer_fields, customer_rules):
        """Valid record should pass validation."""
        validator = generate_validator(simple_file_spec, customer_fields, customer_rules)
        
        record = {
            "customer_id": "123",
            "name": "John Doe",
            "email": "john@example.com",
            "age": "30",
            "status": "active",
        }
        
        result = validator(record)
        assert result.is_valid, f"Errors: {result.errors}"
    
    def test_missing_required_fails(self, simple_file_spec, customer_fields, customer_rules):
        """Missing required field should fail."""
        validator = generate_validator(simple_file_spec, customer_fields, customer_rules)
        
        record = {
            "customer_id": "",
            "name": "John Doe",
            "status": "active",
        }
        
        result = validator(record)
        assert not result.is_valid
        assert any("Customer ID" in e for e in result.errors)
    
    def test_length_violation_fails(self, simple_file_spec, customer_fields, customer_rules):
        """Length violation should fail."""
        validator = generate_validator(simple_file_spec, customer_fields, customer_rules)
        
        record = {
            "customer_id": "123",
            "name": "A",  # Too short
            "status": "active",
        }
        
        result = validator(record)
        assert not result.is_valid
        assert any("2-100" in e for e in result.errors)
    
    def test_range_violation_fails(self, simple_file_spec, customer_fields, customer_rules):
        """Range violation should fail."""
        validator = generate_validator(simple_file_spec, customer_fields, customer_rules)
        
        record = {
            "customer_id": "123",
            "name": "John Doe",
            "age": "200",  # Out of range
            "status": "active",
        }
        
        result = validator(record)
        assert not result.is_valid
        assert any("0-150" in e for e in result.errors)
    
    def test_regex_violation_fails(self, simple_file_spec, customer_fields, customer_rules):
        """Regex violation should fail."""
        validator = generate_validator(simple_file_spec, customer_fields, customer_rules)
        
        record = {
            "customer_id": "123",
            "name": "John Doe",
            "email": "not-an-email",  # Invalid
            "status": "active",
        }
        
        result = validator(record)
        assert not result.is_valid
        assert any("email" in e.lower() for e in result.errors)
    
    def test_enum_violation_fails(self, simple_file_spec, customer_fields, customer_rules):
        """Enum violation should fail."""
        validator = generate_validator(simple_file_spec, customer_fields, customer_rules)
        
        record = {
            "customer_id": "123",
            "name": "John Doe",
            "status": "unknown",  # Not in enum
        }
        
        result = validator(record)
        assert not result.is_valid
        assert any("active" in e for e in result.errors)


class TestGenerateFileValidator:
    """Test file-level validator generation."""
    
    def test_validates_multiple_records(self, simple_file_spec, customer_fields, customer_rules):
        """Should validate multiple records."""
        validator = generate_file_validator(simple_file_spec, customer_fields, customer_rules)
        
        records = [
            {"customer_id": "1", "name": "John", "status": "active"},
            {"customer_id": "2", "name": "Jane", "status": "inactive"},
        ]
        
        result = validator(records)
        assert result.is_valid
    
    def test_unique_rule_detects_duplicates(self, simple_file_spec, customer_fields):
        """Unique rule should detect duplicate values."""
        rules = [
            FileValidationRule(
                id=1, file_spec_id=1, field_name="customer_id",
                rule_type=ValidationRuleType.UNIQUE,
                error_message="Customer ID must be unique"
            ),
        ]
        
        validator = generate_file_validator(simple_file_spec, customer_fields, rules)
        
        records = [
            {"customer_id": "1", "name": "John", "status": "active"},
            {"customer_id": "1", "name": "Jane", "status": "active"},  # Duplicate
        ]
        
        result = validator(records)
        assert not result.is_valid
        assert any("unique" in e.lower() or "duplicate" in e.lower() for e in result.errors)
    
    def test_reports_row_numbers(self, simple_file_spec, customer_fields, customer_rules):
        """Should report row numbers in errors."""
        validator = generate_file_validator(simple_file_spec, customer_fields, customer_rules)
        
        records = [
            {"customer_id": "1", "name": "John", "status": "active"},
            {"customer_id": "", "name": "Jane", "status": "active"},  # Missing required
        ]
        
        result = validator(records)
        assert any("Row 2" in e for e in result.errors)


# ============================================================================
# Code Generation Tests
# ============================================================================

class TestGeneratePythonCode:
    """Test Python code generation."""
    
    def test_generates_valid_python(self, simple_file_spec, customer_fields, customer_rules):
        """Should generate valid Python code."""
        code = generate_python_code(simple_file_spec, customer_fields, customer_rules)
        
        # Should be valid Python
        compile(code, "<generated>", "exec")
    
    def test_includes_docstring(self, simple_file_spec, customer_fields, customer_rules):
        """Generated code should include docstring."""
        code = generate_python_code(simple_file_spec, customer_fields, customer_rules)
        assert "Auto-generated" in code
        assert "customers" in code
    
    def test_generated_code_is_executable(self, simple_file_spec, customer_fields, customer_rules):
        """Generated code should be executable."""
        code = generate_python_code(simple_file_spec, customer_fields, customer_rules)
        
        namespace = {}
        exec(code, namespace)
        
        assert "validate_record" in namespace
        assert callable(namespace["validate_record"])
    
    def test_generated_validator_works(self, simple_file_spec, customer_fields, customer_rules):
        """Generated validator should actually work."""
        code = generate_python_code(simple_file_spec, customer_fields, customer_rules)
        
        namespace = {}
        exec(code, namespace)
        
        validator = namespace["validate_record"]
        
        # Valid record
        result = validator({
            "customer_id": "123",
            "name": "John Doe",
            "status": "active",
        })
        assert result.is_valid
        
        # Invalid record
        result = validator({
            "customer_id": "",
            "name": "J",  # Too short
            "status": "unknown",  # Bad enum
        })
        assert not result.is_valid


class TestGeneratePydanticModel:
    """Test Pydantic model generation."""
    
    def test_generates_valid_python(self, simple_file_spec, customer_fields, customer_rules):
        """Should generate valid Python code."""
        code = generate_pydantic_model(simple_file_spec, customer_fields, customer_rules)
        compile(code, "<generated>", "exec")
    
    def test_includes_class_definition(self, simple_file_spec, customer_fields, customer_rules):
        """Should include class definition."""
        code = generate_pydantic_model(simple_file_spec, customer_fields, customer_rules)
        assert "class" in code
        assert "BaseModel" in code
    
    def test_uses_correct_types(self, simple_file_spec, customer_fields, customer_rules):
        """Should use correct Python types."""
        code = generate_pydantic_model(simple_file_spec, customer_fields, customer_rules)
        assert "int" in code or "Optional[int]" in code
        assert "str" in code or "Optional[str]" in code
    
    def test_custom_model_name(self, simple_file_spec, customer_fields, customer_rules):
        """Should use custom model name if provided."""
        code = generate_pydantic_model(
            simple_file_spec, customer_fields, customer_rules,
            model_name="CustomerRecord"
        )
        assert "class CustomerRecord" in code


# ============================================================================
# Rule Type Tests
# ============================================================================

class TestRequiredRule:
    """Test REQUIRED rule type."""
    
    def test_empty_string_fails(self):
        """Empty string should fail required."""
        spec = FileSpec(id=1, source_system_id=1, name="test", file_type="csv")
        fields = [FileField(id=1, file_spec_id=1, name="field1", field_type="string", position=0)]
        rules = [FileValidationRule(id=1, file_spec_id=1, field_name="field1", rule_type=ValidationRuleType.REQUIRED)]
        
        validator = generate_validator(spec, fields, rules)
        result = validator({"field1": ""})
        assert not result.is_valid
    
    def test_none_fails(self):
        """None should fail required."""
        spec = FileSpec(id=1, source_system_id=1, name="test", file_type="csv")
        fields = [FileField(id=1, file_spec_id=1, name="field1", field_type="string", position=0)]
        rules = [FileValidationRule(id=1, file_spec_id=1, field_name="field1", rule_type=ValidationRuleType.REQUIRED)]
        
        validator = generate_validator(spec, fields, rules)
        result = validator({"field1": None})
        assert not result.is_valid
    
    def test_whitespace_only_fails(self):
        """Whitespace-only string should fail required."""
        spec = FileSpec(id=1, source_system_id=1, name="test", file_type="csv")
        fields = [FileField(id=1, file_spec_id=1, name="field1", field_type="string", position=0)]
        rules = [FileValidationRule(id=1, file_spec_id=1, field_name="field1", rule_type=ValidationRuleType.REQUIRED)]
        
        validator = generate_validator(spec, fields, rules)
        result = validator({"field1": "   "})
        assert not result.is_valid


class TestLengthRule:
    """Test LENGTH rule type."""
    
    def test_below_min_fails(self):
        """String below min length should fail."""
        spec = FileSpec(id=1, source_system_id=1, name="test", file_type="csv")
        fields = [FileField(id=1, file_spec_id=1, name="field1", field_type="string", position=0)]
        rules = [FileValidationRule(
            id=1, file_spec_id=1, field_name="field1",
            rule_type=ValidationRuleType.LENGTH,
            rule_config={"min": 5}
        )]
        
        validator = generate_validator(spec, fields, rules)
        result = validator({"field1": "abc"})
        assert not result.is_valid
    
    def test_above_max_fails(self):
        """String above max length should fail."""
        spec = FileSpec(id=1, source_system_id=1, name="test", file_type="csv")
        fields = [FileField(id=1, file_spec_id=1, name="field1", field_type="string", position=0)]
        rules = [FileValidationRule(
            id=1, file_spec_id=1, field_name="field1",
            rule_type=ValidationRuleType.LENGTH,
            rule_config={"max": 5}
        )]
        
        validator = generate_validator(spec, fields, rules)
        result = validator({"field1": "abcdefgh"})
        assert not result.is_valid
    
    def test_empty_passes_length(self):
        """Empty string should pass length check (not required)."""
        spec = FileSpec(id=1, source_system_id=1, name="test", file_type="csv")
        fields = [FileField(id=1, file_spec_id=1, name="field1", field_type="string", position=0)]
        rules = [FileValidationRule(
            id=1, file_spec_id=1, field_name="field1",
            rule_type=ValidationRuleType.LENGTH,
            rule_config={"min": 5}
        )]
        
        validator = generate_validator(spec, fields, rules)
        result = validator({"field1": ""})
        assert result.is_valid


class TestRangeRule:
    """Test RANGE rule type."""
    
    def test_below_min_fails(self):
        """Value below min should fail."""
        spec = FileSpec(id=1, source_system_id=1, name="test", file_type="csv")
        fields = [FileField(id=1, file_spec_id=1, name="field1", field_type="integer", position=0)]
        rules = [FileValidationRule(
            id=1, file_spec_id=1, field_name="field1",
            rule_type=ValidationRuleType.RANGE,
            rule_config={"min": 10}
        )]
        
        validator = generate_validator(spec, fields, rules)
        result = validator({"field1": "5"})
        assert not result.is_valid
    
    def test_non_numeric_fails(self):
        """Non-numeric value should fail range check."""
        spec = FileSpec(id=1, source_system_id=1, name="test", file_type="csv")
        fields = [FileField(id=1, file_spec_id=1, name="field1", field_type="integer", position=0)]
        rules = [FileValidationRule(
            id=1, file_spec_id=1, field_name="field1",
            rule_type=ValidationRuleType.RANGE,
            rule_config={"min": 0, "max": 100}
        )]
        
        validator = generate_validator(spec, fields, rules)
        result = validator({"field1": "not-a-number"})
        assert not result.is_valid


class TestEnumRule:
    """Test ENUM rule type."""
    
    def test_valid_value_passes(self):
        """Valid enum value should pass."""
        spec = FileSpec(id=1, source_system_id=1, name="test", file_type="csv")
        fields = [FileField(id=1, file_spec_id=1, name="field1", field_type="string", position=0)]
        rules = [FileValidationRule(
            id=1, file_spec_id=1, field_name="field1",
            rule_type=ValidationRuleType.ENUM,
            rule_config={"values": ["A", "B", "C"]}
        )]
        
        validator = generate_validator(spec, fields, rules)
        result = validator({"field1": "A"})
        assert result.is_valid
    
    def test_invalid_value_fails(self):
        """Invalid enum value should fail."""
        spec = FileSpec(id=1, source_system_id=1, name="test", file_type="csv")
        fields = [FileField(id=1, file_spec_id=1, name="field1", field_type="string", position=0)]
        rules = [FileValidationRule(
            id=1, file_spec_id=1, field_name="field1",
            rule_type=ValidationRuleType.ENUM,
            rule_config={"values": ["A", "B", "C"]}
        )]
        
        validator = generate_validator(spec, fields, rules)
        result = validator({"field1": "D"})
        assert not result.is_valid


class TestCrossFieldRule:
    """Test CROSS_FIELD rule type."""
    
    def test_cross_field_validation(self):
        """Cross-field validation should work."""
        spec = FileSpec(id=1, source_system_id=1, name="test", file_type="csv")
        fields = [
            FileField(id=1, file_spec_id=1, name="start_date", field_type="date", position=0),
            FileField(id=2, file_spec_id=1, name="end_date", field_type="date", position=1),
        ]
        rules = [FileValidationRule(
            id=1, file_spec_id=1, field_name=None,  # File-level rule
            rule_type=ValidationRuleType.CROSS_FIELD,
            rule_config={"expression": "start_date <= end_date"},
            error_message="Start date must be before end date"
        )]
        
        validator = generate_validator(spec, fields, rules)
        
        # Valid: start before end
        result = validator({"start_date": "2024-01-01", "end_date": "2024-12-31"})
        assert result.is_valid
        
        # Invalid: start after end
        result = validator({"start_date": "2024-12-31", "end_date": "2024-01-01"})
        assert not result.is_valid
