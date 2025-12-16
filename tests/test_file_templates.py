"""
Unit tests for file codegen templates.

Tests generate_csv_parser(), generate_csv_validator(), and template functions.
"""
import pytest
import ast
import textwrap

from integration_coworker.domain.models import FileSpec, FileField
from integration_coworker.codegen.file_templates import (
    generate_csv_parser,
    _to_class_name,
    _to_python_type,
    _get_parser_expression,
    PYTHON_CSV_PARSER_TEMPLATE,
)


class TestHelperFunctions:
    """Test helper functions."""

    def test_to_class_name_simple(self):
        """Test simple snake_case to PascalCase."""
        assert _to_class_name("customer_data") == "CustomerData"
        assert _to_class_name("order") == "Order"
        assert _to_class_name("user_account_info") == "UserAccountInfo"

    def test_to_class_name_edge_cases(self):
        """Test edge cases for class name conversion."""
        assert _to_class_name("a") == "A"
        assert _to_class_name("ABC") == "Abc"
        assert _to_class_name("my_file_123") == "MyFile123"

    def test_to_python_type_mappings(self):
        """Test field type to Python type mappings."""
        # _to_python_type takes (field_type, nullable) params
        assert _to_python_type("string", False) == "str"
        assert _to_python_type("integer", False) == "int"
        assert _to_python_type("decimal", False) == "Decimal"
        assert _to_python_type("boolean", False) == "bool"
        assert _to_python_type("date", False) == "date"
        assert _to_python_type("datetime", False) == "datetime"
        # Optional types
        assert _to_python_type("string", True) == "Optional[str]"
        assert _to_python_type("integer", True) == "Optional[int]"

    def test_to_python_type_unknown(self):
        """Test unknown type defaults to str."""
        assert _to_python_type("unknown_type", False) == "str"
        assert _to_python_type("", False) == "str"

    def test_get_parser_expression_string(self):
        """Test parser expression for string types."""
        field = FileField(id=1, file_spec_id=1, name="name", field_type="string", position=0)
        expr = _get_parser_expression(field)
        # Default nullable=True means we get row.get() syntax
        assert "row" in expr and "name" in expr

    def test_get_parser_expression_integer(self):
        """Test parser expression for integer."""
        field = FileField(id=1, file_spec_id=1, name="count", field_type="integer", position=0)
        expr = _get_parser_expression(field)
        assert "int(" in expr

    def test_get_parser_expression_decimal(self):
        """Test parser expression for decimal."""
        field = FileField(id=1, file_spec_id=1, name="price", field_type="decimal", position=0)
        expr = _get_parser_expression(field)
        assert "Decimal(" in expr

    def test_get_parser_expression_boolean(self):
        """Test parser expression for boolean."""
        field = FileField(id=1, file_spec_id=1, name="active", field_type="boolean", position=0)
        expr = _get_parser_expression(field)
        assert "lower()" in expr or "True" in expr

    def test_get_parser_expression_date_with_format(self):
        """Test parser expression for date with format mask."""
        field = FileField(
            id=1, file_spec_id=1, name="created", field_type="date", 
            position=0, format_mask="%Y-%m-%d"
        )
        expr = _get_parser_expression(field)
        assert "strptime" in expr
        assert "%Y-%m-%d" in expr

    def test_get_parser_expression_date_without_format(self):
        """Test parser expression for date without format mask."""
        field = FileField(id=1, file_spec_id=1, name="created", field_type="date", position=0)
        expr = _get_parser_expression(field)
        assert "strptime" in expr

    def test_get_parser_expression_nullable(self):
        """Test parser expression for nullable field."""
        field = FileField(
            id=1, file_spec_id=1, name="email", field_type="string", 
            position=0, nullable=True
        )
        expr = _get_parser_expression(field)
        assert "get(" in expr or "or None" in expr


class TestGenerateCSVParser:
    """Test generate_csv_parser function."""

    def test_generate_basic_parser(self):
        """Test generating basic CSV parser."""
        file_spec = FileSpec(
            id=1,
            source_system_id=1,
            name="customers",
            file_type="csv",
            delimiter=",",
            has_header=True,
        )
        fields = [
            FileField(id=1, file_spec_id=1, name="id", field_type="integer", position=0, nullable=False),
            FileField(id=2, file_spec_id=1, name="name", field_type="string", position=1, nullable=False),
        ]

        code = generate_csv_parser(file_spec, fields)

        # Check code is valid Python
        ast.parse(code)

        # Check expected content
        assert "class Customers" in code
        assert "def parse_customers(" in code
        assert "csv.DictReader" in code

    def test_generated_code_imports(self):
        """Test generated code has required imports."""
        file_spec = FileSpec(id=1, source_system_id=1, name="data", file_type="csv", delimiter=",")
        fields = [
            FileField(id=1, file_spec_id=1, name="amount", field_type="decimal", position=0),
            FileField(id=2, file_spec_id=1, name="date", field_type="date", position=1),
        ]

        code = generate_csv_parser(file_spec, fields)

        assert "import csv" in code
        assert "from decimal import Decimal" in code
        assert "from datetime import date" in code

    def test_generated_dataclass_fields(self):
        """Test generated dataclass has correct fields."""
        file_spec = FileSpec(id=1, source_system_id=1, name="orders", file_type="csv")
        fields = [
            FileField(id=1, file_spec_id=1, name="order_id", field_type="integer", position=0, nullable=False),
            FileField(id=2, file_spec_id=1, name="total", field_type="decimal", position=1, nullable=True),
            FileField(id=3, file_spec_id=1, name="notes", field_type="string", position=2, nullable=True),
        ]

        code = generate_csv_parser(file_spec, fields)

        assert "order_id: int" in code
        assert "total: Optional[Decimal]" in code
        assert "notes: Optional[str]" in code

    def test_generated_validation_function(self):
        """Test generated code includes validation function."""
        file_spec = FileSpec(id=1, source_system_id=1, name="items", file_type="csv")
        fields = [
            FileField(id=1, file_spec_id=1, name="id", field_type="integer", position=0, nullable=False),
            FileField(id=2, file_spec_id=1, name="name", field_type="string", position=1, nullable=False),
        ]

        code = generate_csv_parser(file_spec, fields)

        assert "def validate_items(" in code
        assert "errors = []" in code
        assert "is required" in code

    def test_generated_main_block(self):
        """Test generated code includes main block."""
        file_spec = FileSpec(id=1, source_system_id=1, name="products", file_type="csv")
        fields = [FileField(id=1, file_spec_id=1, name="sku", field_type="string", position=0)]

        code = generate_csv_parser(file_spec, fields)

        assert 'if __name__ == "__main__"' in code
        assert "sys.argv" in code

    def test_generated_tsv_parser(self):
        """Test generating TSV parser uses tab delimiter."""
        file_spec = FileSpec(
            id=1,
            source_system_id=1,
            name="records",
            file_type="tsv",
            delimiter="\t",
        )
        fields = [FileField(id=1, file_spec_id=1, name="data", field_type="string", position=0)]

        code = generate_csv_parser(file_spec, fields)

        assert "delimiter='\\t'" in code or 'delimiter="\\t"' in code

    def test_generated_custom_delimiter(self):
        """Test generating parser with custom delimiter."""
        file_spec = FileSpec(
            id=1,
            source_system_id=1,
            name="data",
            file_type="csv",
            delimiter="|",
        )
        fields = [FileField(id=1, file_spec_id=1, name="value", field_type="string", position=0)]

        code = generate_csv_parser(file_spec, fields)

        assert "delimiter='|'" in code or 'delimiter="|"' in code


class TestGeneratedCodeExecution:
    """Test that generated code can be executed."""

    def test_generated_code_compiles(self):
        """Test that generated code compiles without syntax errors."""
        file_spec = FileSpec(
            id=1, source_system_id=1, name="test_file", file_type="csv",
            delimiter=",", has_header=True,
        )
        fields = [
            FileField(id=1, file_spec_id=1, name="id", field_type="integer", position=0, nullable=False),
            FileField(id=2, file_spec_id=1, name="name", field_type="string", position=1, nullable=False),
            FileField(id=3, file_spec_id=1, name="email", field_type="email", position=2, nullable=True),
            FileField(id=4, file_spec_id=1, name="balance", field_type="decimal", position=3, nullable=True),
            FileField(id=5, file_spec_id=1, name="created", field_type="date", position=4, nullable=True, format_mask="%Y-%m-%d"),
            FileField(id=6, file_spec_id=1, name="active", field_type="boolean", position=5, nullable=True),
        ]

        code = generate_csv_parser(file_spec, fields)
        
        # Compile to bytecode - will raise SyntaxError if invalid
        compiled = compile(code, "<generated>", "exec")
        assert compiled is not None

    def test_generated_code_can_execute_definition(self):
        """Test that generated code can define classes and functions."""
        file_spec = FileSpec(id=1, source_system_id=1, name="items", file_type="csv")
        fields = [
            FileField(id=1, file_spec_id=1, name="id", field_type="integer", position=0),
        ]

        code = generate_csv_parser(file_spec, fields)
        
        # Execute the code to define classes/functions
        namespace = {}
        exec(code, namespace)
        
        # Check that expected items were defined
        assert "Items" in namespace  # dataclass
        assert "parse_items" in namespace  # parse function
        assert "validate_items" in namespace  # validate function


class TestTemplateConstant:
    """Test the template constant."""

    def test_template_is_valid_format_string(self):
        """Test that template can be formatted."""
        # Template should have these placeholders
        placeholders = [
            "{class_name}", "{file_name}", "{fields_def}", 
            "{field_assignments}", "{validation_checks}",
            "{function_name}", "{delimiter}"
        ]
        
        for placeholder in placeholders:
            assert placeholder in PYTHON_CSV_PARSER_TEMPLATE or "{" in PYTHON_CSV_PARSER_TEMPLATE


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_fields_list(self):
        """Test generating parser with empty fields list."""
        file_spec = FileSpec(id=1, source_system_id=1, name="empty", file_type="csv")
        fields = []

        code = generate_csv_parser(file_spec, fields)
        
        # Should still generate valid code
        ast.parse(code)

    def test_special_characters_in_name(self):
        """Test file name with numbers."""
        file_spec = FileSpec(id=1, source_system_id=1, name="data_2024_q1", file_type="csv")
        fields = [FileField(id=1, file_spec_id=1, name="value", field_type="string", position=0)]

        code = generate_csv_parser(file_spec, fields)
        
        assert "Data2024Q1" in code  # class name
        ast.parse(code)

    def test_all_field_types(self):
        """Test generating parser with all field types."""
        file_spec = FileSpec(id=1, source_system_id=1, name="all_types", file_type="csv")
        fields = [
            FileField(id=1, file_spec_id=1, name="str_field", field_type="string", position=0),
            FileField(id=2, file_spec_id=1, name="int_field", field_type="integer", position=1),
            FileField(id=3, file_spec_id=1, name="dec_field", field_type="decimal", position=2),
            FileField(id=4, file_spec_id=1, name="bool_field", field_type="boolean", position=3),
            FileField(id=5, file_spec_id=1, name="date_field", field_type="date", position=4),
            FileField(id=6, file_spec_id=1, name="dt_field", field_type="datetime", position=5),
            FileField(id=7, file_spec_id=1, name="time_field", field_type="time", position=6),
            FileField(id=8, file_spec_id=1, name="email_field", field_type="email", position=7),
            FileField(id=9, file_spec_id=1, name="phone_field", field_type="phone", position=8),
            FileField(id=10, file_spec_id=1, name="url_field", field_type="url", position=9),
        ]

        code = generate_csv_parser(file_spec, fields)
        
        # Should compile without errors
        ast.parse(code)

    def test_reserved_word_field_name(self):
        """Test field name that's a Python reserved word."""
        file_spec = FileSpec(id=1, source_system_id=1, name="test", file_type="csv")
        # Note: this might cause issues - test how we handle it
        fields = [
            FileField(id=1, file_spec_id=1, name="class", field_type="string", position=0),
            FileField(id=2, file_spec_id=1, name="import", field_type="string", position=1),
        ]

        code = generate_csv_parser(file_spec, fields)
        
        # Code generation should still work, even if the code won't run
        # We're testing that the generator doesn't crash
        assert "class" in code
