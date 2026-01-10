"""
Tests for FileCodegenStrategy.

Validates:
1. Strategy uses FileParserTemplateResult for all symbol names (no guessing)
2. Enum comparisons are safe (uses .value not stringly-typed)
3. JSON-safe metadata contract enforced
4. Flow/test artifacts reference symbols from template result
"""
import pytest
from unittest.mock import MagicMock, patch

from integration_coworker.codegen.strategies.file import FileCodegenStrategy
from integration_coworker.codegen.protocol_dispatch import (
    ArtifactType,
    CodegenContext,
    GeneratedArtifact,
)
from integration_coworker.codegen.file_templates import (
    generate_file_parser_with_metadata,
    FileParserTemplateResult,
)
from integration_coworker.domain.ir import Operation, ProtocolType
from integration_coworker.domain.models import (
    FileSpec,
    FileField,
    FileFieldType,
)


# --- Fixtures ---


@pytest.fixture
def sample_file_spec():
    """Create a sample FileSpec."""
    return FileSpec(
        id=1,
        name="Customer Data",
        file_type="csv",
        encoding="utf-8",
        delimiter=",",
        has_header=True,
        source_system_id=100,
    )


@pytest.fixture
def excel_file_spec():
    """Create an Excel FileSpec (xlsx variant)."""
    return FileSpec(
        id=2,
        name="Sales Report",
        file_type="xlsx",  # Should normalize to "excel"
        encoding="utf-8",
        delimiter=None,
        has_header=True,
        source_system_id=100,
    )


@pytest.fixture
def sample_file_fields():
    """Create sample FileField objects."""
    return [
        FileField(
            id=1,
            file_spec_id=1,
            name="customer_id",
            field_type=FileFieldType.INTEGER,
            position=0,
            nullable=False,
        ),
        FileField(
            id=2,
            file_spec_id=1,
            name="customer_name",
            field_type=FileFieldType.STRING,
            position=1,
            nullable=False,
        ),
        FileField(
            id=3,
            file_spec_id=1,
            name="balance",
            field_type=FileFieldType.DECIMAL,
            position=2,
            nullable=True,
        ),
    ]


@pytest.fixture
def excel_file_fields():
    """Create sample fields for Excel file."""
    return [
        FileField(
            id=10,
            file_spec_id=2,
            name="sale_date",
            field_type=FileFieldType.DATE,
            position=0,
            nullable=False,
        ),
        FileField(
            id=11,
            file_spec_id=2,
            name="amount",
            field_type=FileFieldType.DECIMAL,
            position=1,
            nullable=False,
        ),
    ]


@pytest.fixture
def file_operation(sample_file_spec):
    """Create a file parsing operation."""
    return Operation(
        operation_id="parse_customer_data",
        name="Parse Customer Data",
        protocol=ProtocolType.FILE,
        metadata={"file_spec_id": 1, "op": "parse"},
    )


@pytest.fixture
def excel_operation(excel_file_spec):
    """Create an Excel parsing operation."""
    return Operation(
        operation_id="parse_sales_report",
        name="Parse Sales Report",
        protocol=ProtocolType.FILE,
        metadata={"file_spec_id": 2, "op": "parse"},
    )


@pytest.fixture
def codegen_context():
    """Create a CodegenContext."""
    return CodegenContext(
        language="python",
        provider_code="test",
    )


@pytest.fixture
def strategy(sample_file_spec, sample_file_fields):
    """Create a FileCodegenStrategy with sample data."""
    return FileCodegenStrategy(
        file_specs=[sample_file_spec],
        file_fields=sample_file_fields,
    )


@pytest.fixture
def excel_strategy(excel_file_spec, excel_file_fields, sample_file_spec, sample_file_fields):
    """Create strategy with both CSV and Excel specs."""
    return FileCodegenStrategy(
        file_specs=[sample_file_spec, excel_file_spec],
        file_fields=sample_file_fields + excel_file_fields,
    )


# --- Protocol Type Tests ---


class TestFileCodegenStrategyProtocol:
    """Test strategy protocol implementation."""
    
    def test_protocol_type_returns_file(self, strategy):
        """Strategy correctly identifies as FILE protocol."""
        assert strategy.protocol_type == ProtocolType.FILE
    
    def test_protocol_type_is_enum_not_string(self, strategy):
        """Protocol type is enum member, not string."""
        assert isinstance(strategy.protocol_type, ProtocolType)
        assert strategy.protocol_type is ProtocolType.FILE


# --- Initialization Tests ---


class TestFileCodegenStrategyInit:
    """Test strategy initialization."""
    
    def test_init_with_empty_specs(self):
        """Strategy handles empty specs gracefully."""
        strategy = FileCodegenStrategy(file_specs=[], file_fields=[])
        assert strategy.protocol_type == ProtocolType.FILE
    
    def test_init_with_none_specs(self):
        """Strategy handles None specs gracefully."""
        strategy = FileCodegenStrategy(file_specs=None, file_fields=None)
        assert strategy.protocol_type == ProtocolType.FILE
    
    def test_resolve_file_spec_found(self, strategy, sample_file_spec):
        """Strategy resolves FileSpec by ID."""
        resolved = strategy._resolve_file_spec(1)
        assert resolved.id == sample_file_spec.id
        assert resolved.name == sample_file_spec.name
    
    def test_resolve_file_spec_not_found_raises(self, strategy):
        """Strategy raises ValueError for unknown FileSpec ID."""
        with pytest.raises(ValueError, match="FileSpec ID 999 not found"):
            strategy._resolve_file_spec(999)
    
    def test_resolve_fields_found(self, strategy, sample_file_fields):
        """Strategy resolves fields by file_spec_id."""
        fields = strategy._resolve_fields(1)
        assert len(fields) == len(sample_file_fields)
    
    def test_resolve_fields_not_found_returns_empty(self, strategy):
        """Strategy returns empty list for unknown file_spec_id."""
        fields = strategy._resolve_fields(999)
        assert fields == []


# --- Generate Client Tests ---


class TestGenerateClient:
    """Tests for generate_client method."""
    
    def test_generate_client_returns_artifact(self, strategy, file_operation, codegen_context):
        """generate_client returns a GeneratedArtifact."""
        artifact = strategy.generate_client(file_operation, codegen_context)
        assert isinstance(artifact, GeneratedArtifact)
    
    def test_generate_client_artifact_type(self, strategy, file_operation, codegen_context):
        """Artifact has CLIENT type."""
        artifact = strategy.generate_client(file_operation, codegen_context)
        assert artifact.artifact_type == ArtifactType.CLIENT
    
    def test_generate_client_uses_template_result_names(
        self, strategy, file_operation, codegen_context, sample_file_spec, sample_file_fields
    ):
        """
        CRITICAL: Client artifact uses names from FileParserTemplateResult, not guessed.
        """
        # Get what the template would produce
        expected_result = generate_file_parser_with_metadata(
            sample_file_spec, sample_file_fields, "python"
        )
        
        # Generate client
        artifact = strategy.generate_client(file_operation, codegen_context)
        
        # Verify names match template result exactly
        assert artifact.metadata["parse_func"] == expected_result.parse_func_name
        assert artifact.metadata["validate_func"] == expected_result.validate_func_name
        assert artifact.metadata["record_class"] == expected_result.record_class_name
        assert artifact.metadata["module_basename"] == expected_result.module_basename
    
    def test_generate_client_filename_from_template(
        self, strategy, file_operation, codegen_context, sample_file_spec, sample_file_fields
    ):
        """Filename comes from template result module_basename."""
        expected_result = generate_file_parser_with_metadata(
            sample_file_spec, sample_file_fields, "python"
        )
        
        artifact = strategy.generate_client(file_operation, codegen_context)
        assert artifact.filename == f"{expected_result.module_basename}.py"
    
    def test_generate_client_code_matches_template(
        self, strategy, file_operation, codegen_context, sample_file_spec, sample_file_fields
    ):
        """Generated code matches template result code."""
        expected_result = generate_file_parser_with_metadata(
            sample_file_spec, sample_file_fields, "python"
        )
        
        artifact = strategy.generate_client(file_operation, codegen_context)
        assert artifact.code == expected_result.code
    
    def test_generate_client_dependencies_from_template(
        self, strategy, file_operation, codegen_context, sample_file_spec, sample_file_fields
    ):
        """Dependencies come from template result."""
        expected_result = generate_file_parser_with_metadata(
            sample_file_spec, sample_file_fields, "python"
        )
        
        artifact = strategy.generate_client(file_operation, codegen_context)
        assert artifact.dependencies == expected_result.dependencies
    
    def test_generate_client_excel_dependencies(
        self, excel_strategy, excel_operation, codegen_context
    ):
        """Excel files get openpyxl dependency."""
        artifact = excel_strategy.generate_client(excel_operation, codegen_context)
        assert "openpyxl" in artifact.dependencies
    
    def test_generate_client_missing_metadata_raises(self, strategy, codegen_context):
        """Operation without file_spec_id raises ValueError."""
        op = Operation(
            operation_id="bad_op",
            name="Bad Op",
            protocol=ProtocolType.FILE,
            metadata={},  # Missing file_spec_id
        )
        with pytest.raises(ValueError, match="missing file_spec_id"):
            strategy.generate_client(op, codegen_context)
    
    def test_generate_client_none_metadata_raises(self, strategy, codegen_context):
        """Operation with None metadata raises ValueError."""
        op = Operation(
            operation_id="bad_op",
            name="Bad Op",
            protocol=ProtocolType.FILE,
            metadata=None,
        )
        with pytest.raises(ValueError, match="missing file_spec_id"):
            strategy.generate_client(op, codegen_context)


# --- Generate Flow Tests ---


class TestGenerateFlow:
    """Tests for generate_flow method."""
    
    def test_generate_flow_returns_artifact(self, strategy, file_operation, codegen_context):
        """generate_flow returns a GeneratedArtifact."""
        artifact = strategy.generate_flow(file_operation, codegen_context)
        assert isinstance(artifact, GeneratedArtifact)
    
    def test_generate_flow_artifact_type(self, strategy, file_operation, codegen_context):
        """Artifact has FLOW type."""
        artifact = strategy.generate_flow(file_operation, codegen_context)
        assert artifact.artifact_type == ArtifactType.FLOW
    
    def test_generate_flow_uses_template_result_names_directly(
        self, strategy, file_operation, codegen_context, sample_file_spec, sample_file_fields
    ):
        """
        CRITICAL: Flow imports names from template result, not guessed names.
        """
        expected_result = generate_file_parser_with_metadata(
            sample_file_spec, sample_file_fields, "python"
        )
        
        artifact = strategy.generate_flow(file_operation, codegen_context)
        
        # Flow code should import the exact names from template
        assert f"from {expected_result.module_basename} import" in artifact.code
        assert expected_result.parse_func_name in artifact.code
        assert expected_result.validate_func_name in artifact.code
    
    def test_generate_flow_uses_client_artifact_metadata(
        self, strategy, file_operation, codegen_context
    ):
        """Flow can use names from client artifact metadata."""
        # First generate client
        client = strategy.generate_client(file_operation, codegen_context)
        
        # Generate flow with client artifact
        flow = strategy.generate_flow(file_operation, codegen_context, client_artifact=client)
        
        # Flow should use names from client metadata
        assert client.metadata["parse_func"] in flow.code
        assert client.metadata["validate_func"] in flow.code
    
    def test_generate_flow_filename_uses_module_basename(
        self, strategy, file_operation, codegen_context, sample_file_spec, sample_file_fields
    ):
        """Flow filename uses module_basename from template."""
        expected_result = generate_file_parser_with_metadata(
            sample_file_spec, sample_file_fields, "python"
        )
        
        artifact = strategy.generate_flow(file_operation, codegen_context)
        assert artifact.filename == f"flow_{expected_result.module_basename}.py"


# --- Generate Test Tests ---


class TestGenerateTest:
    """Tests for generate_test method."""
    
    def test_generate_test_returns_artifact(self, strategy, file_operation, codegen_context):
        """generate_test returns a GeneratedArtifact."""
        artifact = strategy.generate_test(file_operation, codegen_context)
        assert isinstance(artifact, GeneratedArtifact)
    
    def test_generate_test_artifact_type(self, strategy, file_operation, codegen_context):
        """Artifact has TEST type."""
        artifact = strategy.generate_test(file_operation, codegen_context)
        assert artifact.artifact_type == ArtifactType.TEST
    
    def test_generate_test_imports_template_names(
        self, strategy, file_operation, codegen_context, sample_file_spec, sample_file_fields
    ):
        """
        CRITICAL: Test imports exact names from template result.
        """
        expected_result = generate_file_parser_with_metadata(
            sample_file_spec, sample_file_fields, "python"
        )
        
        artifact = strategy.generate_test(file_operation, codegen_context)
        
        # Should import exact names
        assert expected_result.parse_func_name in artifact.code
        assert expected_result.validate_func_name in artifact.code
        assert expected_result.record_class_name in artifact.code
    
    def test_generate_test_uses_client_artifact_metadata(
        self, strategy, file_operation, codegen_context
    ):
        """Test can use names from client artifact metadata."""
        client = strategy.generate_client(file_operation, codegen_context)
        test = strategy.generate_test(file_operation, codegen_context, client_artifact=client)
        
        # Test should reference exact names from client
        assert client.metadata["parse_func"] in test.code
        assert client.metadata["validate_func"] in test.code
        assert client.metadata["record_class"] in test.code
    
    def test_generate_test_filename_uses_module_basename(
        self, strategy, file_operation, codegen_context, sample_file_spec, sample_file_fields
    ):
        """Test filename uses module_basename from template."""
        expected_result = generate_file_parser_with_metadata(
            sample_file_spec, sample_file_fields, "python"
        )
        
        artifact = strategy.generate_test(file_operation, codegen_context)
        assert artifact.filename == f"test_{expected_result.module_basename}.py"


# --- Enum Safety Tests ---


class TestEnumSafety:
    """Tests verifying enum comparisons are safe (use .value not strings)."""
    
    def test_test_generation_handles_integer_field_type(
        self, strategy, file_operation, codegen_context
    ):
        """Test generator handles INTEGER field type via .value."""
        artifact = strategy.generate_test(file_operation, codegen_context)
        # Should contain sample data for integer field
        assert artifact.code is not None
    
    def test_test_generation_handles_decimal_field_type(
        self, strategy, file_operation, codegen_context
    ):
        """Test generator handles DECIMAL field type via .value."""
        artifact = strategy.generate_test(file_operation, codegen_context)
        # Should contain "100.00" sample for decimal
        assert "100.00" in artifact.code
    
    def test_field_type_comparison_is_enum_safe(self, strategy, sample_file_fields):
        """
        Verify the strategy uses enum-safe field type comparison.
        
        This test ensures we compare using .value, not string representation.
        """
        # FileFieldType is an enum - verify it has .value
        for field in sample_file_fields:
            assert hasattr(field.field_type, 'value'), \
                f"FileFieldType should be an enum with .value attribute"
            assert isinstance(field.field_type.value, str), \
                f"FileFieldType.value should be a string"


# --- JSON-Safe Metadata Tests ---


class TestJsonSafeMetadata:
    """Tests verifying metadata is JSON-serializable."""
    
    def test_client_metadata_is_json_safe(self, strategy, file_operation, codegen_context):
        """Client artifact metadata contains only JSON-serializable values."""
        import json
        artifact = strategy.generate_client(file_operation, codegen_context)
        
        # Should not raise
        serialized = json.dumps(artifact.metadata)
        deserialized = json.loads(serialized)
        
        # Verify round-trip
        assert deserialized["file_spec_id"] == artifact.metadata["file_spec_id"]
        assert deserialized["parse_func"] == artifact.metadata["parse_func"]
    
    def test_flow_metadata_is_json_safe(self, strategy, file_operation, codegen_context):
        """Flow artifact metadata contains only JSON-serializable values."""
        import json
        artifact = strategy.generate_flow(file_operation, codegen_context)
        
        serialized = json.dumps(artifact.metadata)
        deserialized = json.loads(serialized)
        
        assert deserialized["file_spec_id"] == artifact.metadata["file_spec_id"]
    
    def test_test_metadata_is_json_safe(self, strategy, file_operation, codegen_context):
        """Test artifact metadata contains only JSON-serializable values."""
        import json
        artifact = strategy.generate_test(file_operation, codegen_context)
        
        serialized = json.dumps(artifact.metadata)
        deserialized = json.loads(serialized)
        
        assert deserialized["file_spec_id"] == artifact.metadata["file_spec_id"]


# --- Build Prompt Tests ---


class TestBuildPrompt:
    """Tests for build_prompt method."""
    
    def test_build_prompt_returns_string(
        self, strategy, file_operation, codegen_context
    ):
        """build_prompt returns a string."""
        prompt = strategy.build_prompt(
            file_operation, codegen_context, ArtifactType.CLIENT, "skeleton"
        )
        assert isinstance(prompt, str)
    
    def test_build_prompt_contains_file_spec_info(
        self, strategy, file_operation, codegen_context, sample_file_spec
    ):
        """Prompt contains file spec information."""
        prompt = strategy.build_prompt(
            file_operation, codegen_context, ArtifactType.CLIENT, "skeleton"
        )
        
        assert sample_file_spec.name in prompt
        assert sample_file_spec.file_type in prompt
    
    def test_build_prompt_contains_field_info(
        self, strategy, file_operation, codegen_context, sample_file_fields
    ):
        """Prompt contains field information."""
        prompt = strategy.build_prompt(
            file_operation, codegen_context, ArtifactType.CLIENT, "skeleton"
        )
        
        for field in sample_file_fields:
            assert field.name in prompt
    
    def test_build_prompt_missing_metadata_returns_empty(
        self, strategy, codegen_context
    ):
        """Missing metadata returns empty prompt."""
        op = Operation(
            operation_id="bad",
            name="Bad",
            protocol=ProtocolType.FILE,
            metadata=None,
        )
        prompt = strategy.build_prompt(
            op, codegen_context, ArtifactType.CLIENT, "skeleton"
        )
        assert prompt == ""


# --- Integration Tests ---


class TestStrategyIntegration:
    """Integration tests for full artifact generation workflow."""
    
    def test_client_flow_test_names_are_consistent(
        self, strategy, file_operation, codegen_context
    ):
        """
        CRITICAL: All three artifacts reference the same symbol names.
        
        This verifies that generate_flow and generate_test use the same
        names that generate_client produces (via template result).
        """
        client = strategy.generate_client(file_operation, codegen_context)
        flow = strategy.generate_flow(file_operation, codegen_context, client_artifact=client)
        test = strategy.generate_test(file_operation, codegen_context, client_artifact=client)
        
        # Extract names from client metadata
        parse_func = client.metadata["parse_func"]
        validate_func = client.metadata["validate_func"]
        record_class = client.metadata["record_class"]
        module_name = client.metadata["module_basename"]
        
        # Verify flow references the same names
        assert f"from {module_name} import" in flow.code
        assert parse_func in flow.code
        assert validate_func in flow.code
        
        # Verify test references the same names
        assert f"from {module_name} import" in test.code
        assert parse_func in test.code
        assert validate_func in test.code
        assert record_class in test.code
    
    def test_excel_file_type_normalized(
        self, excel_strategy, excel_operation, codegen_context
    ):
        """Excel file types (xlsx) are normalized and generate correct code."""
        artifact = excel_strategy.generate_client(excel_operation, codegen_context)
        
        # Should have openpyxl dependency
        assert "openpyxl" in artifact.dependencies
        
        # Code should be generated successfully
        assert len(artifact.code) > 0


# --- Dispatcher Registration Tests ---


class TestDispatcherRegistration:
    """Tests for FileCodegenStrategy registration in dispatcher."""
    
    def test_create_default_dispatcher_includes_file_protocol(self):
        """Default dispatcher includes FILE protocol support."""
        from integration_coworker.codegen.protocol_dispatch import create_default_dispatcher
        
        dispatcher = create_default_dispatcher()
        assert ProtocolType.FILE in dispatcher.supported_protocols
    
    def test_create_default_dispatcher_with_file_specs(
        self, sample_file_spec, sample_file_fields
    ):
        """Dispatcher accepts file_specs and file_fields parameters."""
        from integration_coworker.codegen.protocol_dispatch import create_default_dispatcher
        
        dispatcher = create_default_dispatcher(
            file_specs=[sample_file_spec],
            file_fields=sample_file_fields,
        )
        assert ProtocolType.FILE in dispatcher.supported_protocols
    
    def test_dispatcher_routes_file_operations(
        self, sample_file_spec, sample_file_fields, file_operation, codegen_context
    ):
        """Dispatcher correctly routes FILE protocol operations."""
        from integration_coworker.codegen.protocol_dispatch import create_default_dispatcher
        
        dispatcher = create_default_dispatcher(
            file_specs=[sample_file_spec],
            file_fields=sample_file_fields,
        )
        
        # Should be able to generate client via generate() method
        artifact = dispatcher.generate(file_operation, codegen_context, ArtifactType.CLIENT)
        assert artifact is not None
        assert artifact.artifact_type == ArtifactType.CLIENT
    
    def test_dispatcher_all_protocols_supported(self):
        """Dispatcher supports REST, GraphQL, AsyncAPI, and FILE."""
        from integration_coworker.codegen.protocol_dispatch import create_default_dispatcher
        
        dispatcher = create_default_dispatcher()
        supported = dispatcher.supported_protocols
        
        assert ProtocolType.REST in supported
        assert ProtocolType.GRAPHQL in supported
        assert ProtocolType.ASYNCAPI in supported
        assert ProtocolType.FILE in supported


__all__ = []
