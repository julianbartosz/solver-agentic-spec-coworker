"""
Tests for csv_schema module.

Tests CSV/TSV schema inference and JSON Schema conversion.
"""
import pytest
from integration_coworker.parsers.csv_schema import (
    infer_csv_schema,
    csv_schema_to_json_schema,
    CsvSchema,
    InferredField,
)


@pytest.mark.no_db
class TestInferCsvSchema:
    """Test CSV schema inference."""
    
    def test_simple_csv_with_header(self):
        """Test inference from simple CSV with header."""
        content = """name,age,email
John,30,john@example.com
Jane,25,jane@example.com"""
        
        schema = infer_csv_schema(content)
        
        assert len(schema.fields) == 3
        assert schema.has_header is True
        assert schema.row_count == 2
        
        field_names = [f.name for f in schema.fields]
        assert "name" in field_names
        assert "age" in field_names
        assert "email" in field_names
    
    def test_infer_integer_type(self):
        """Test integer type inference."""
        content = """id,count
1,100
2,200
3,300"""
        
        schema = infer_csv_schema(content)
        
        id_field = next(f for f in schema.fields if f.name == "id")
        count_field = next(f for f in schema.fields if f.name == "count")
        
        assert id_field.inferred_type == "integer"
        assert count_field.inferred_type == "integer"
    
    def test_infer_number_type(self):
        """Test decimal number type inference."""
        content = """product,price
Widget,19.99
Gadget,29.50"""
        
        schema = infer_csv_schema(content)
        
        price_field = next(f for f in schema.fields if f.name == "price")
        assert price_field.inferred_type == "number"
    
    def test_infer_boolean_type(self):
        """Test boolean type inference."""
        content = """item,active,enabled
A,true,yes
B,false,no"""
        
        schema = infer_csv_schema(content)
        
        active_field = next(f for f in schema.fields if f.name == "active")
        enabled_field = next(f for f in schema.fields if f.name == "enabled")
        
        assert active_field.inferred_type == "boolean"
        assert enabled_field.inferred_type == "boolean"
    
    def test_infer_date_type(self):
        """Test date type inference."""
        content = """event,date
Meeting,2024-01-15
Conference,2024-02-20"""
        
        schema = infer_csv_schema(content)
        
        date_field = next(f for f in schema.fields if f.name == "date")
        assert date_field.inferred_type == "date"
    
    def test_infer_datetime_type(self):
        """Test datetime type inference."""
        content = """event,timestamp
Login,2024-01-15T10:30:00Z
Logout,2024-01-15T17:45:00Z"""
        
        schema = infer_csv_schema(content)
        
        timestamp_field = next(f for f in schema.fields if f.name == "timestamp")
        assert timestamp_field.inferred_type == "datetime"
    
    def test_infer_email_type(self):
        """Test email type inference."""
        content = """user,email
john,john@example.com
jane,jane@company.org"""
        
        schema = infer_csv_schema(content)
        
        email_field = next(f for f in schema.fields if f.name == "email")
        assert email_field.inferred_type == "email"
    
    def test_infer_uuid_type(self):
        """Test UUID type inference."""
        content = """entity,uuid
A,550e8400-e29b-41d4-a716-446655440000
B,6ba7b810-9dad-11d1-80b4-00c04fd430c8"""
        
        schema = infer_csv_schema(content)
        
        uuid_field = next(f for f in schema.fields if f.name == "uuid")
        assert uuid_field.inferred_type == "uuid"
    
    def test_infer_nullable_field(self):
        """Test nullable field detection."""
        content = """name,middle_name
John,William
Jane,"""
        
        schema = infer_csv_schema(content)
        
        middle_name_field = next(f for f in schema.fields if f.name == "middle_name")
        assert middle_name_field.nullable is True
    
    def test_auto_detect_comma_delimiter(self):
        """Test auto-detection of comma delimiter."""
        content = """a,b,c
1,2,3"""
        
        schema = infer_csv_schema(content)
        
        assert schema.delimiter == ","
        assert len(schema.fields) == 3
    
    def test_auto_detect_tab_delimiter(self):
        """Test auto-detection of tab delimiter."""
        content = "a\tb\tc\n1\t2\t3"
        
        schema = infer_csv_schema(content)
        
        assert schema.delimiter == "\t"
        assert len(schema.fields) == 3
    
    def test_auto_detect_semicolon_delimiter(self):
        """Test auto-detection of semicolon delimiter."""
        content = """a;b;c
1;2;3"""
        
        schema = infer_csv_schema(content)
        
        assert schema.delimiter == ";"
        assert len(schema.fields) == 3
    
    def test_no_header_mode(self):
        """Test inference without header row."""
        content = """John,30,john@example.com
Jane,25,jane@example.com"""
        
        schema = infer_csv_schema(content, has_header=False)
        
        assert schema.has_header is False
        assert len(schema.fields) == 3
        # Should generate column names
        assert schema.fields[0].name.startswith("column_")
    
    def test_explicit_delimiter(self):
        """Test explicit delimiter override."""
        content = """a|b|c
1|2|3"""
        
        schema = infer_csv_schema(content, delimiter="|")
        
        assert schema.delimiter == "|"
        assert len(schema.fields) == 3
    
    def test_sample_values_collected(self):
        """Test that sample values are collected."""
        content = """color
red
blue
green"""
        
        schema = infer_csv_schema(content)
        
        color_field = next(f for f in schema.fields if f.name == "color")
        assert "red" in color_field.sample_values
        assert "blue" in color_field.sample_values
    
    def test_column_name_normalization(self):
        """Test that column names are normalized."""
        content = """First Name,Last-Name,User ID
John,Doe,123"""
        
        schema = infer_csv_schema(content)
        
        field_names = [f.name for f in schema.fields]
        assert "first_name" in field_names
        assert "last_name" in field_names
        assert "user_id" in field_names
    
    def test_empty_content(self):
        """Test handling of empty content."""
        schema = infer_csv_schema("")
        
        assert len(schema.errors) > 0
        assert "Empty" in schema.errors[0]
    
    def test_row_count_tracking(self):
        """Test row count tracking."""
        content = """a
1
2
3
4
5"""
        
        schema = infer_csv_schema(content)
        
        assert schema.row_count == 5


@pytest.mark.no_db
class TestCsvSchemaToJsonSchema:
    """Test JSON Schema conversion."""
    
    def test_basic_conversion(self):
        """Test basic conversion to JSON Schema."""
        csv_schema = CsvSchema(
            fields=[
                InferredField(name="name", inferred_type="string", nullable=False),
                InferredField(name="age", inferred_type="integer", nullable=True),
            ]
        )
        
        json_schema = csv_schema_to_json_schema(csv_schema)
        
        assert json_schema["type"] == "object"
        assert "properties" in json_schema
        assert "name" in json_schema["properties"]
        assert json_schema["properties"]["name"]["type"] == "string"
    
    def test_required_fields(self):
        """Test required fields in JSON Schema."""
        csv_schema = CsvSchema(
            fields=[
                InferredField(name="id", inferred_type="integer", nullable=False),
                InferredField(name="notes", inferred_type="string", nullable=True),
            ]
        )
        
        json_schema = csv_schema_to_json_schema(csv_schema)
        
        assert "required" in json_schema
        assert "id" in json_schema["required"]
        assert "notes" not in json_schema["required"]
    
    def test_nullable_types(self):
        """Test nullable type representation."""
        csv_schema = CsvSchema(
            fields=[
                InferredField(name="optional", inferred_type="string", nullable=True),
            ]
        )
        
        json_schema = csv_schema_to_json_schema(csv_schema)
        
        optional_type = json_schema["properties"]["optional"]["type"]
        # Should be array with null
        assert isinstance(optional_type, list)
        assert "null" in optional_type
        assert "string" in optional_type
    
    def test_format_types(self):
        """Test format types in JSON Schema."""
        csv_schema = CsvSchema(
            fields=[
                InferredField(name="created_at", inferred_type="datetime", nullable=False),
                InferredField(name="birth_date", inferred_type="date", nullable=False),
                InferredField(name="email", inferred_type="email", nullable=False),
                InferredField(name="id", inferred_type="uuid", nullable=False),
            ]
        )
        
        json_schema = csv_schema_to_json_schema(csv_schema)
        
        assert json_schema["properties"]["created_at"]["format"] == "date-time"
        assert json_schema["properties"]["birth_date"]["format"] == "date"
        assert json_schema["properties"]["email"]["format"] == "email"
        assert json_schema["properties"]["id"]["format"] == "uuid"
    
    def test_schema_version(self):
        """Test JSON Schema version is included."""
        csv_schema = CsvSchema(fields=[])
        
        json_schema = csv_schema_to_json_schema(csv_schema)
        
        assert "$schema" in json_schema


@pytest.mark.no_db
class TestCsvSchemaDataclass:
    """Test CsvSchema dataclass."""
    
    def test_create_csv_schema(self):
        """Test creating a CsvSchema."""
        schema = CsvSchema(
            fields=[InferredField(name="test", inferred_type="string")],
            row_count=10,
            has_header=True,
            delimiter=","
        )
        
        assert len(schema.fields) == 1
        assert schema.row_count == 10
        assert schema.has_header is True
        assert schema.delimiter == ","
    
    def test_csv_schema_defaults(self):
        """Test CsvSchema default values."""
        schema = CsvSchema()
        
        assert schema.fields == []
        assert schema.row_count == 0
        assert schema.has_header is True
        assert schema.delimiter == ","
        assert schema.errors == []


@pytest.mark.no_db
class TestInferredFieldDataclass:
    """Test InferredField dataclass."""
    
    def test_create_inferred_field(self):
        """Test creating an InferredField."""
        field = InferredField(
            name="test",
            inferred_type="integer",
            nullable=False,
            sample_values=["1", "2", "3"]
        )
        
        assert field.name == "test"
        assert field.inferred_type == "integer"
        assert field.nullable is False
        assert field.sample_values == ["1", "2", "3"]
    
    def test_inferred_field_defaults(self):
        """Test InferredField default values."""
        field = InferredField(name="test", inferred_type="string")
        
        assert field.nullable is True
        assert field.sample_values == []
        assert field.description is None
