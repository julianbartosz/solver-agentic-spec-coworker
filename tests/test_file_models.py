"""
Unit tests for file domain models.

Tests FileSpec, FileField, RecordLayout, FileValidationRule, FileFieldMapping models.
"""
import pytest
from dataclasses import asdict, fields

from integration_coworker.domain.models import (
    FileSpec,
    FileField,
    RecordLayout,
    FileValidationRule,
    FileFieldMapping,
    FileType,
    FileFieldType,
    ValidationRuleType,
    KGNodeType,
    KGEdgeRelation,
)


class TestFileSpecModel:
    """Test FileSpec dataclass."""

    def test_create_minimal_file_spec(self):
        """Test creating FileSpec with required fields only."""
        spec = FileSpec(
            id=1,
            source_system_id=1,
            name="customer_data",
            file_type="csv",
        )
        assert spec.id == 1
        assert spec.source_system_id == 1
        assert spec.name == "customer_data"
        assert spec.file_type == "csv"

    def test_create_full_file_spec(self):
        """Test creating FileSpec with all fields."""
        spec = FileSpec(
            id=1,
            source_system_id=1,
            name="customer_data",
            file_type="csv",
            description="Customer master data file",
            delimiter=",",
            has_header=True,
            encoding="utf-8",
            line_terminator="\n",
            quote_char='"',
            escape_char="\\",
            version="1.0",
            sample_uri="/samples/customer_data.csv",
        )
        assert spec.description == "Customer master data file"
        assert spec.delimiter == ","
        assert spec.has_header is True
        assert spec.encoding == "utf-8"
        assert spec.quote_char == '"'
        assert spec.escape_char == "\\"
        assert spec.version == "1.0"
        assert spec.sample_uri == "/samples/customer_data.csv"

    def test_file_spec_asdict(self):
        """Test FileSpec can be converted to dict."""
        spec = FileSpec(
            id=1,
            source_system_id=1,
            name="orders",
            file_type="tsv",
            delimiter="\t",
        )
        d = asdict(spec)
        assert d["name"] == "orders"
        assert d["delimiter"] == "\t"

    def test_file_spec_optional_defaults(self):
        """Test FileSpec optional field defaults."""
        spec = FileSpec(
            id=1,
            source_system_id=1,
            name="test",
            file_type="csv",
        )
        # Check defaults from the actual model
        assert spec.spec_document_id is None
        assert spec.encoding == "utf-8"  # Has default value
        assert spec.has_header is True  # Has default value True
        assert spec.line_terminator == "\n"  # Has default
        assert spec.description is None
        assert spec.version is None


class TestFileFieldModel:
    """Test FileField dataclass."""

    def test_create_minimal_file_field(self):
        """Test creating FileField with required fields only."""
        field = FileField(
            id=1,
            file_spec_id=1,
            name="customer_id",
            field_type="integer",
            position=0,
        )
        assert field.id == 1
        assert field.file_spec_id == 1
        assert field.name == "customer_id"
        assert field.field_type == "integer"
        assert field.position == 0

    def test_create_full_file_field(self):
        """Test creating FileField with all fields."""
        field = FileField(
            id=1,
            file_spec_id=1,
            name="birth_date",
            field_type="date",
            position=5,
            description="Customer date of birth",
            nullable=True,
            length=10,
            start_position=50,  # For fixed-width files
            format_mask="%Y-%m-%d",
            default_value="1900-01-01",
            validation_regex=r"^\d{4}-\d{2}-\d{2}$",
            sample_values=["2024-01-15", "2023-12-01"],
            inference_confidence=0.95,
        )
        assert field.description == "Customer date of birth"
        assert field.nullable is True
        assert field.length == 10
        assert field.format_mask == "%Y-%m-%d"
        assert field.default_value == "1900-01-01"
        assert field.start_position == 50
        assert field.inference_confidence == 0.95

    def test_file_field_fixed_width(self):
        """Test FileField with fixed-width positioning."""
        field = FileField(
            id=1,
            file_spec_id=1,
            name="account_number",
            field_type="string",
            position=0,
            start_position=1,
            length=10,
        )
        assert field.start_position == 1
        assert field.length == 10

    def test_file_field_asdict(self):
        """Test FileField can be converted to dict."""
        field = FileField(
            id=1,
            file_spec_id=1,
            name="email",
            field_type="string",
            position=2,
            nullable=True,
        )
        d = asdict(field)
        assert d["name"] == "email"
        assert d["nullable"] is True


class TestRecordLayoutModel:
    """Test RecordLayout dataclass."""

    def test_create_record_layout(self):
        """Test creating RecordLayout."""
        layout = RecordLayout(
            id=1,
            file_spec_id=1,
            record_type="detail",
            identifier_field="record_type_code",
            identifier_value="D",
            description="Detail record type",
            record_length=100,
            position=0,
            min_occurrences=1,
            max_occurrences=None,
        )
        assert layout.id == 1
        assert layout.file_spec_id == 1
        assert layout.record_type == "detail"
        assert layout.identifier_field == "record_type_code"
        assert layout.identifier_value == "D"
        assert layout.description == "Detail record type"
        assert layout.record_length == 100

    def test_record_layout_optional_fields(self):
        """Test RecordLayout with minimal fields."""
        layout = RecordLayout(
            id=1,
            file_spec_id=1,
            record_type="header",
        )
        assert layout.identifier_field is None
        assert layout.identifier_value is None
        assert layout.record_length is None
        assert layout.position == 0  # Has default
        assert layout.min_occurrences == 0  # Has default


class TestFileValidationRuleModel:
    """Test FileValidationRule dataclass."""

    def test_create_validation_rule(self):
        """Test creating FileValidationRule."""
        rule = FileValidationRule(
            id=1,
            file_spec_id=1,
            field_name="customer_code",
            rule_type="regex",
            rule_config={"pattern": "^[A-Z]{2}[0-9]{6}$"},
            error_message="Invalid customer code format",
        )
        assert rule.id == 1
        assert rule.file_spec_id == 1
        assert rule.field_name == "customer_code"
        assert rule.rule_type == "regex"
        assert rule.error_message == "Invalid customer code format"

    def test_validation_rule_range(self):
        """Test validation rule for range check."""
        rule = FileValidationRule(
            id=2,
            file_spec_id=1,
            field_name="amount",
            rule_type="range",
            rule_config={"min": 0, "max": 1000000},
            error_message="Amount must be between 0 and 1000000",
        )
        assert rule.rule_type == "range"

    def test_validation_rule_optional_message(self):
        """Test validation rule without error message."""
        rule = FileValidationRule(
            id=1,
            file_spec_id=1,
            rule_type="required",
        )
        assert rule.error_message is None
        assert rule.rule_config == {}  # Gets defaulted by __post_init__


class TestFileFieldMappingModel:
    """Test FileFieldMapping dataclass."""

    def test_create_field_mapping(self):
        """Test creating FileFieldMapping."""
        mapping = FileFieldMapping(
            id=1,
            file_field_id=1,
            entity_id=10,
            entity_field_name="customer_name",
            transform_expression="UPPER(value)",
        )
        assert mapping.id == 1
        assert mapping.file_field_id == 1
        assert mapping.entity_id == 10
        assert mapping.entity_field_name == "customer_name"
        assert mapping.transform_expression == "UPPER(value)"

    def test_field_mapping_minimal(self):
        """Test creating minimal FileFieldMapping."""
        mapping = FileFieldMapping(
            id=1,
            file_field_id=5,
            entity_id=15,
            entity_field_name="value",
        )
        assert mapping.transform_expression is None


class TestFileTypeEnum:
    """Test FileType enum."""

    def test_file_types_exist(self):
        """Test all expected file types exist."""
        assert FileType.CSV.value == "csv"
        assert FileType.TSV.value == "tsv"
        assert FileType.FIXED_WIDTH.value == "fixed_width"
        assert FileType.XLSX.value == "xlsx"
        assert FileType.XLS.value == "xls"
        assert FileType.PIPE_DELIMITED.value == "pipe_delimited"
        assert FileType.EDI_X12.value == "edi_x12"
        assert FileType.EDIFACT.value == "edifact"

    def test_file_type_from_string(self):
        """Test creating FileType from string."""
        ft = FileType("csv")
        assert ft == FileType.CSV


class TestFileFieldTypeEnum:
    """Test FileFieldType enum."""

    def test_field_types_exist(self):
        """Test all expected field types exist."""
        assert FileFieldType.STRING.value == "string"
        assert FileFieldType.INTEGER.value == "integer"
        assert FileFieldType.DECIMAL.value == "decimal"
        assert FileFieldType.BOOLEAN.value == "boolean"
        assert FileFieldType.DATE.value == "date"
        assert FileFieldType.DATETIME.value == "datetime"
        assert FileFieldType.EMAIL.value == "email"
        assert FileFieldType.UUID.value == "uuid"
        assert FileFieldType.JSON.value == "json"
        assert FileFieldType.BINARY.value == "binary"


class TestValidationRuleTypeEnum:
    """Test ValidationRuleType enum."""

    def test_validation_rule_types_exist(self):
        """Test all expected validation rule types exist."""
        assert ValidationRuleType.REQUIRED.value == "required"
        assert ValidationRuleType.REGEX.value == "regex"
        assert ValidationRuleType.RANGE.value == "range"
        assert ValidationRuleType.LENGTH.value == "length"
        assert ValidationRuleType.ENUM.value == "enum"
        assert ValidationRuleType.UNIQUE.value == "unique"
        assert ValidationRuleType.LOOKUP.value == "lookup"
        assert ValidationRuleType.FORMAT.value == "format"
        assert ValidationRuleType.CROSS_FIELD.value == "cross_field"


class TestKGNodeTypeExtensions:
    """Test KGNodeType extensions for files."""

    def test_file_node_types_exist(self):
        """Test file-related KG node types exist."""
        assert KGNodeType.FILE_SPEC.value == "file_spec"
        assert KGNodeType.FILE_FIELD.value == "file_field"
        assert KGNodeType.RECORD_LAYOUT.value == "record_layout"
        assert KGNodeType.FILE_PATTERN.value == "file_pattern"


class TestKGEdgeRelationExtensions:
    """Test KGEdgeRelation extensions for files."""

    def test_file_edge_relations_exist(self):
        """Test file-related KG edge relations exist."""
        assert KGEdgeRelation.HAS_FIELD.value == "has_field"
        assert KGEdgeRelation.MAPS_TO.value == "maps_to"
        assert KGEdgeRelation.VALIDATED_BY.value == "validated_by"
        assert KGEdgeRelation.DERIVES_FROM_GUIDE.value == "derives_from_guide"
        assert KGEdgeRelation.FILE_FLOWS_TO.value == "file_flows_to"


class TestFileModelsSerialization:
    """Test serialization compatibility."""

    def test_file_spec_all_fields(self):
        """Test FileSpec has expected fields count."""
        spec_fields = fields(FileSpec)
        field_names = {f.name for f in spec_fields}
        
        required = {"id", "source_system_id", "name", "file_type"}
        assert required.issubset(field_names)

    def test_file_field_all_fields(self):
        """Test FileField has expected fields count."""
        field_fields = fields(FileField)
        field_names = {f.name for f in field_fields}
        
        required = {"id", "file_spec_id", "name", "field_type", "position"}
        assert required.issubset(field_names)

    def test_file_spec_to_dict(self):
        """Test FileSpec.to_dict() method."""
        spec = FileSpec(id=1, source_system_id=1, name="test", file_type="csv")
        d = spec.to_dict()
        assert d["id"] == 1
        assert d["name"] == "test"
        assert "file_type" in d
        
    def test_file_field_to_dict(self):
        """Test FileField.to_dict() method."""
        field = FileField(id=1, file_spec_id=1, name="col1", field_type="string", position=0)
        d = field.to_dict()
        assert d["id"] == 1
        assert d["name"] == "col1"
        assert "position" in d
        
    def test_record_layout_to_dict(self):
        """Test RecordLayout.to_dict() method."""
        layout = RecordLayout(id=1, file_spec_id=1, record_type="header")
        d = layout.to_dict()
        assert d["id"] == 1
        assert d["record_type"] == "header"
        
    def test_validation_rule_to_dict(self):
        """Test FileValidationRule.to_dict() method."""
        rule = FileValidationRule(id=1, file_spec_id=1, rule_type="required")
        d = rule.to_dict()
        assert d["id"] == 1
        assert d["rule_type"] == "required"
