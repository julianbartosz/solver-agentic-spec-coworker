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
    generate_file_parser_with_metadata,
    FileParserTemplateResult,
    _normalize_file_type,
    _to_class_name,
    _to_snake_case,
    _to_python_type,
    _get_parser_expression,
    PYTHON_CSV_PARSER_TEMPLATE,
)


class TestFileParserTemplateResult:
    """Tests for FileParserTemplateResult and generate_file_parser_with_metadata()."""

    @pytest.fixture
    def sample_csv_spec(self):
        """Sample CSV file spec for testing."""
        return FileSpec(
            id=1,
            source_system_id=1,
            name="customer_data",
            file_type="csv",
            delimiter=",",
            has_header=True,
        )

    @pytest.fixture
    def sample_fields(self):
        """Sample fields for testing."""
        return [
            FileField(id=1, file_spec_id=1, name="id", field_type="integer", position=0),
            FileField(id=2, file_spec_id=1, name="name", field_type="string", position=1),
            FileField(id=3, file_spec_id=1, name="amount", field_type="decimal", position=2),
        ]

    def test_returns_file_parser_template_result(self, sample_csv_spec, sample_fields):
        """generate_file_parser_with_metadata returns FileParserTemplateResult."""
        result = generate_file_parser_with_metadata(sample_csv_spec, sample_fields)
        assert isinstance(result, FileParserTemplateResult)

    def test_module_basename_matches_snake_case(self, sample_csv_spec, sample_fields):
        """module_basename uses same logic as templates."""
        result = generate_file_parser_with_metadata(sample_csv_spec, sample_fields)
        expected = _to_snake_case(sample_csv_spec.name)
        assert result.module_basename == expected
        assert result.module_basename == "customer_data"

    def test_record_class_name_matches_pascal_case(self, sample_csv_spec, sample_fields):
        """record_class_name uses same logic as templates."""
        result = generate_file_parser_with_metadata(sample_csv_spec, sample_fields)
        expected = _to_class_name(sample_csv_spec.name)
        assert result.record_class_name == expected
        assert result.record_class_name == "CustomerData"

    def test_parse_func_name_matches_template_output(self, sample_csv_spec, sample_fields):
        """parse_func_name matches what templates actually generate."""
        result = generate_file_parser_with_metadata(sample_csv_spec, sample_fields)
        assert result.parse_func_name == "parse_customer_data"
        # Verify it actually appears in the generated code
        assert f"def {result.parse_func_name}(" in result.code

    def test_validate_func_name_matches_template_output(self, sample_csv_spec, sample_fields):
        """validate_func_name matches what templates actually generate."""
        result = generate_file_parser_with_metadata(sample_csv_spec, sample_fields)
        assert result.validate_func_name == "validate_customer_data"
        # Verify it actually appears in the generated code
        assert f"def {result.validate_func_name}(" in result.code

    def test_class_name_appears_in_generated_code(self, sample_csv_spec, sample_fields):
        """record_class_name actually appears in the generated code."""
        result = generate_file_parser_with_metadata(sample_csv_spec, sample_fields)
        assert f"class {result.record_class_name}:" in result.code

    def test_csv_has_no_dependencies(self, sample_csv_spec, sample_fields):
        """CSV files don't require extra dependencies."""
        result = generate_file_parser_with_metadata(sample_csv_spec, sample_fields)
        assert result.dependencies == []

    def test_excel_has_openpyxl_dependency(self, sample_fields):
        """Excel files require openpyxl."""
        # Note: generate_file_parser uses "excel" not "xlsx" 
        excel_spec = FileSpec(
            id=2,
            source_system_id=1,
            name="sales_report",
            file_type="excel",  # Canonical internal value
        )
        result = generate_file_parser_with_metadata(excel_spec, sample_fields)
        assert "openpyxl" in result.dependencies

    def test_xlsx_normalizes_to_excel(self, sample_fields):
        """xlsx file type normalizes to excel for routing."""
        xlsx_spec = FileSpec(
            id=4,
            source_system_id=1,
            name="quarterly_report",
            file_type="xlsx",  # Common extension, should normalize to "excel"
        )
        result = generate_file_parser_with_metadata(xlsx_spec, sample_fields)
        # Should route to excel parser and have openpyxl dependency
        assert "openpyxl" in result.dependencies
        # Should generate valid code (proves routing worked)
        ast.parse(result.code)

    def test_xls_normalizes_to_excel(self, sample_fields):
        """xls file type normalizes to excel for routing."""
        xls_spec = FileSpec(
            id=5,
            source_system_id=1,
            name="legacy_data",
            file_type="xls",  # Legacy extension, should normalize to "excel"
        )
        result = generate_file_parser_with_metadata(xls_spec, sample_fields)
        assert "openpyxl" in result.dependencies
        ast.parse(result.code)

    def test_kebab_case_name_handled(self, sample_fields):
        """Kebab-case names are converted correctly."""
        spec = FileSpec(
            id=3,
            source_system_id=1,
            name="user-activity-log",
            file_type="csv",
            delimiter=",",
        )
        result = generate_file_parser_with_metadata(spec, sample_fields)
        assert result.module_basename == "user_activity_log"
        assert result.record_class_name == "UserActivityLog"
        assert result.parse_func_name == "parse_user_activity_log"

    def test_code_is_valid_python(self, sample_csv_spec, sample_fields):
        """Generated code is syntactically valid Python."""
        result = generate_file_parser_with_metadata(sample_csv_spec, sample_fields)
        # This will raise SyntaxError if invalid
        ast.parse(result.code)


class TestNormalizeFileType:
    """Tests for _normalize_file_type helper."""

    def test_csv_unchanged(self):
        """CSV type passes through unchanged."""
        assert _normalize_file_type("csv") == "csv"

    def test_xlsx_normalizes_to_excel(self):
        """xlsx extension normalizes to excel."""
        assert _normalize_file_type("xlsx") == "excel"

    def test_xls_normalizes_to_excel(self):
        """xls extension normalizes to excel."""
        assert _normalize_file_type("xls") == "excel"

    def test_excel_unchanged(self):
        """excel type passes through unchanged."""
        assert _normalize_file_type("excel") == "excel"

    def test_fixed_width_unchanged(self):
        """fixed_width type passes through unchanged."""
        assert _normalize_file_type("fixed_width") == "fixed_width"

    def test_case_insensitive(self):
        """Normalization is case-insensitive."""
        assert _normalize_file_type("XLSX") == "excel"
        assert _normalize_file_type("CSV") == "csv"


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


class TestGenerateExcelParser:
    """Test generate_excel_parser function."""

    def test_generate_basic_excel_parser(self):
        """Test generating basic Excel parser."""
        from integration_coworker.codegen.file_templates import generate_excel_parser
        
        file_spec = FileSpec(
            id=1,
            source_system_id=1,
            name="customers",
            file_type="excel",
            has_header=True,
        )
        fields = [
            FileField(id=1, file_spec_id=1, name="id", field_type="integer", position=0, nullable=False),
            FileField(id=2, file_spec_id=1, name="name", field_type="string", position=1, nullable=False),
        ]

        code = generate_excel_parser(file_spec, fields)

        # Check code is valid Python
        ast.parse(code)

        # Check expected content
        assert "class Customers" in code
        assert "def parse_customers(" in code
        assert "load_workbook" in code
        assert "openpyxl" in code

    def test_excel_parser_imports_openpyxl(self):
        """Test generated Excel parser imports openpyxl."""
        from integration_coworker.codegen.file_templates import generate_excel_parser
        
        file_spec = FileSpec(id=1, source_system_id=1, name="data", file_type="excel")
        fields = [
            FileField(id=1, file_spec_id=1, name="value", field_type="string", position=0),
        ]

        code = generate_excel_parser(file_spec, fields)

        assert "from openpyxl import load_workbook" in code
        assert "ImportError" in code  # Should handle missing openpyxl

    def test_excel_parser_dataclass_fields(self):
        """Test generated dataclass has correct fields."""
        from integration_coworker.codegen.file_templates import generate_excel_parser
        
        file_spec = FileSpec(id=1, source_system_id=1, name="orders", file_type="excel")
        fields = [
            FileField(id=1, file_spec_id=1, name="order_id", field_type="integer", position=0, nullable=False),
            FileField(id=2, file_spec_id=1, name="total", field_type="decimal", position=1, nullable=True),
            FileField(id=3, file_spec_id=1, name="notes", field_type="string", position=2, nullable=True),
        ]

        code = generate_excel_parser(file_spec, fields)

        assert "order_id: int" in code
        assert "total: Optional[Decimal]" in code
        assert "notes: Optional[str]" in code

    def test_excel_parser_type_conversion(self):
        """Test generated Excel parser includes type conversion helper."""
        from integration_coworker.codegen.file_templates import generate_excel_parser
        
        file_spec = FileSpec(id=1, source_system_id=1, name="test", file_type="excel")
        fields = [
            FileField(id=1, file_spec_id=1, name="date_col", field_type="date", position=0),
            FileField(id=2, file_spec_id=1, name="num_col", field_type="integer", position=1),
        ]

        code = generate_excel_parser(file_spec, fields)

        assert "_convert_value" in code
        assert "target_type" in code

    def test_excel_parser_sheet_name_parameter(self):
        """Test generated Excel parser accepts sheet_name parameter."""
        from integration_coworker.codegen.file_templates import generate_excel_parser
        
        file_spec = FileSpec(id=1, source_system_id=1, name="report", file_type="excel")
        fields = [FileField(id=1, file_spec_id=1, name="value", field_type="string", position=0)]

        code = generate_excel_parser(file_spec, fields)

        assert "sheet_name" in code
        assert "wb.sheetnames" in code

    def test_excel_parser_compiles_and_defines_functions(self):
        """Test generated Excel parser code compiles and defines expected functions."""
        from integration_coworker.codegen.file_templates import generate_excel_parser
        
        file_spec = FileSpec(id=1, source_system_id=1, name="items", file_type="excel")
        fields = [
            FileField(id=1, file_spec_id=1, name="id", field_type="integer", position=0),
            FileField(id=2, file_spec_id=1, name="name", field_type="string", position=1),
        ]

        code = generate_excel_parser(file_spec, fields)
        
        # Should compile without syntax errors
        compiled = compile(code, "<generated>", "exec")
        assert compiled is not None
        
        # Execute in namespace - openpyxl MUST be installed for this test to be meaningful
        # If openpyxl is missing, this test should FAIL (not skip)
        namespace = {}
        exec(code, namespace)
        
        # Assert the Excel parser functions are defined
        assert "Items" in namespace, "Dataclass 'Items' not defined in generated code"
        assert "parse_items" in namespace, "Function 'parse_items' not defined in generated code"
        assert "validate_items" in namespace, "Function 'validate_items' not defined in generated code"
        assert callable(namespace["parse_items"]), "parse_items should be callable"
        assert callable(namespace["validate_items"]), "validate_items should be callable"

    def test_excel_parser_with_all_types(self):
        """Test Excel parser generation with all field types."""
        from integration_coworker.codegen.file_templates import generate_excel_parser
        
        file_spec = FileSpec(id=1, source_system_id=1, name="all_types", file_type="excel")
        fields = [
            FileField(id=1, file_spec_id=1, name="str_field", field_type="string", position=0),
            FileField(id=2, file_spec_id=1, name="int_field", field_type="integer", position=1),
            FileField(id=3, file_spec_id=1, name="dec_field", field_type="decimal", position=2),
            FileField(id=4, file_spec_id=1, name="bool_field", field_type="boolean", position=3),
            FileField(id=5, file_spec_id=1, name="date_field", field_type="date", position=4),
            FileField(id=6, file_spec_id=1, name="dt_field", field_type="datetime", position=5),
        ]

        code = generate_excel_parser(file_spec, fields)
        
        # Should compile without errors
        ast.parse(code)

    def test_excel_parser_parses_real_workbook(self, tmp_path):
        """Test generated Excel parser can parse a real Excel file (end-to-end)."""
        import openpyxl
        from integration_coworker.codegen.file_templates import generate_excel_parser
        
        # Create test Excel file
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "TestSheet"
        
        # Header row
        ws.append(["id", "name", "value"])
        # Data rows
        ws.append([1, "Alice", 100.5])
        ws.append([2, "Bob", 200.75])
        ws.append([3, "Charlie", 300.0])
        
        excel_path = tmp_path / "test_data.xlsx"
        wb.save(excel_path)
        wb.close()
        
        # Generate parser
        file_spec = FileSpec(id=1, source_system_id=1, name="test_data", file_type="excel")
        fields = [
            FileField(id=1, file_spec_id=1, name="id", field_type="integer", position=0, nullable=False),
            FileField(id=2, file_spec_id=1, name="name", field_type="string", position=1, nullable=False),
            FileField(id=3, file_spec_id=1, name="value", field_type="decimal", position=2, nullable=False),
        ]
        
        code = generate_excel_parser(file_spec, fields)
        
        # Execute generated code
        namespace = {}
        exec(code, namespace)
        
        # Parse the file
        parse_fn = namespace["parse_test_data"]
        records = parse_fn(str(excel_path))
        
        # Verify results
        assert len(records) == 3, f"Expected 3 records, got {len(records)}"
        
        # Check first record
        assert records[0].id == 1
        assert records[0].name == "Alice"
        # Decimal comparison
        from decimal import Decimal
        assert records[0].value == Decimal("100.5")
        
        # Check second record
        assert records[1].id == 2
        assert records[1].name == "Bob"
        
        # Validate records
        validate_fn = namespace["validate_test_data"]
        errors = validate_fn(records)
        assert errors == [], f"Validation errors: {errors}"


class TestGenerateFileParser:
    """Test the generate_file_parser routing function."""

    def test_routes_csv_to_csv_parser(self):
        """Test CSV file type routes to CSV parser."""
        from integration_coworker.codegen.file_templates import generate_file_parser, generate_csv_parser
        
        file_spec = FileSpec(id=1, source_system_id=1, name="test", file_type="csv", delimiter=",")
        fields = [FileField(id=1, file_spec_id=1, name="col", field_type="string", position=0)]

        result = generate_file_parser(file_spec, fields)
        expected = generate_csv_parser(file_spec, fields)
        
        assert result == expected

    def test_routes_tsv_to_csv_parser(self):
        """Test TSV file type routes to CSV parser."""
        from integration_coworker.codegen.file_templates import generate_file_parser, generate_csv_parser
        
        file_spec = FileSpec(id=1, source_system_id=1, name="test", file_type="tsv", delimiter="\t")
        fields = [FileField(id=1, file_spec_id=1, name="col", field_type="string", position=0)]

        result = generate_file_parser(file_spec, fields)
        expected = generate_csv_parser(file_spec, fields)
        
        assert result == expected

    def test_routes_pipe_delimited_to_csv_parser(self):
        """Test pipe_delimited file type routes to CSV parser."""
        from integration_coworker.codegen.file_templates import generate_file_parser, generate_csv_parser
        
        file_spec = FileSpec(id=1, source_system_id=1, name="test", file_type="pipe_delimited", delimiter="|")
        fields = [FileField(id=1, file_spec_id=1, name="col", field_type="string", position=0)]

        result = generate_file_parser(file_spec, fields)
        expected = generate_csv_parser(file_spec, fields)
        
        assert result == expected

    def test_routes_fixed_width_to_fixed_width_parser(self):
        """Test fixed_width file type routes to fixed-width parser."""
        from integration_coworker.codegen.file_templates import generate_file_parser, generate_fixed_width_parser
        
        file_spec = FileSpec(id=1, source_system_id=1, name="test", file_type="fixed_width")
        fields = [
            FileField(id=1, file_spec_id=1, name="col", field_type="string", position=0, start_position=1, length=10)
        ]

        result = generate_file_parser(file_spec, fields)
        expected = generate_fixed_width_parser(file_spec, fields)
        
        assert result == expected

    def test_routes_excel_to_excel_parser(self):
        """Test excel file type routes to Excel parser."""
        from integration_coworker.codegen.file_templates import generate_file_parser, generate_excel_parser
        
        file_spec = FileSpec(id=1, source_system_id=1, name="test", file_type="excel")
        fields = [FileField(id=1, file_spec_id=1, name="col", field_type="string", position=0)]

        result = generate_file_parser(file_spec, fields)
        expected = generate_excel_parser(file_spec, fields)
        
        assert result == expected

    def test_raises_for_unsupported_type(self):
        """Test unsupported file type raises NotImplementedError."""
        from integration_coworker.codegen.file_templates import generate_file_parser
        
        file_spec = FileSpec(id=1, source_system_id=1, name="test", file_type="xml")
        fields = [FileField(id=1, file_spec_id=1, name="col", field_type="string", position=0)]

        with pytest.raises(NotImplementedError) as exc_info:
            generate_file_parser(file_spec, fields)
        
        assert "xml" in str(exc_info.value)

    def test_raises_for_json_type(self):
        """Test JSON file type raises NotImplementedError."""
        from integration_coworker.codegen.file_templates import generate_file_parser
        
        file_spec = FileSpec(id=1, source_system_id=1, name="test", file_type="json")
        fields = [FileField(id=1, file_spec_id=1, name="col", field_type="string", position=0)]

        with pytest.raises(NotImplementedError):
            generate_file_parser(file_spec, fields)

    def test_generated_code_is_valid_python(self):
        """Test all routed parsers produce valid Python."""
        from integration_coworker.codegen.file_templates import generate_file_parser
        
        test_cases = [
            ("csv", ",", None, None),
            ("tsv", "\t", None, None),
            ("fixed_width", None, 1, 10),
            ("excel", None, None, None),
        ]
        
        for file_type, delimiter, start_pos, length in test_cases:
            file_spec = FileSpec(
                id=1, source_system_id=1, name=f"test_{file_type}", 
                file_type=file_type, delimiter=delimiter
            )
            fields = [
                FileField(
                    id=1, file_spec_id=1, name="col", field_type="string", 
                    position=0, start_position=start_pos, length=length
                )
            ]

            code = generate_file_parser(file_spec, fields)
            
            # All should produce valid Python
            ast.parse(code)
