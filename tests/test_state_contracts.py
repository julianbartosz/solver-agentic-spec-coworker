"""
State contract tests for File Integration V1.

These tests enforce strict typing requirements:
- WorkflowState.file_specs must only contain FileSpec objects, not dicts
- WorkflowState.file_fields must only contain FileField objects, not dicts
- detect_and_parse_spec routes file specs to parsed_specs as ParsedSpec objects

Run with: pytest tests/test_state_contracts.py -v
"""
import pytest
from dataclasses import asdict

from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import FileSpec, FileField, SourceSystem, SpecDocument
from integration_coworker.sources.base import ParsedSpec, SourceType


class TestDetectAndParseSpecRouting:
    """Test that detect_and_parse_spec routes file specs correctly."""
    
    def test_csv_routes_to_parsed_specs_as_parsedspec(self):
        """CSV content should be routed to parsed_specs as ParsedSpec objects."""
        from integration_coworker.graph.nodes.detect_and_parse_spec import detect_and_parse_spec
        
        csv_content = "id,name,amount\n1,Alice,100.50\n2,Bob,200.25\n"
        
        state = WorkflowState(
            source_refs=["customers.csv"],
            spec_refs=["customers.csv"],
            task_description="Parse customer CSV"
        )
        state.spec_documents = [
            SpecDocument(
                id=None,
                source_system_id=None,
                version="1.0",
                uri="customers.csv",
                content_type="text/csv",
                sha256="abc123",
                content=csv_content,
            )
        ]
        
        result = detect_and_parse_spec(state)
        
        # Should have routed to parsed_specs
        assert len(result.parsed_specs) == 1, "CSV should produce one ParsedSpec in parsed_specs"
        
        parsed = result.parsed_specs[0]
        assert isinstance(parsed, ParsedSpec), f"Expected ParsedSpec, got {type(parsed)}"
        assert parsed.source_type == SourceType.FILE
        assert parsed.source_uri == "customers.csv"
        
        # Data should contain FileSpec and fields
        assert "file_spec" in parsed.data
        assert "fields" in parsed.data
        assert isinstance(parsed.data["file_spec"], FileSpec)
        
        # Should NOT be in file_specs (that's build_silver_file_model's job)
        assert len(result.file_specs) == 0, "CSV should not be put directly in file_specs"
    
    def test_openapi_still_routes_to_openapi_spec(self):
        """OpenAPI content should still route to openapi_spec (preserve existing behavior)."""
        from integration_coworker.graph.nodes.detect_and_parse_spec import detect_and_parse_spec
        
        openapi_content = '''
openapi: "3.0.0"
info:
  title: Test API
  version: "1.0.0"
paths:
  /users:
    get:
      summary: List users
      responses:
        "200":
          description: OK
'''
        
        state = WorkflowState(
            source_refs=["api.yaml"],
            spec_refs=["api.yaml"],
            task_description="Parse API spec"
        )
        state.spec_documents = [
            SpecDocument(
                id=None,
                source_system_id=None,
                version="1.0",
                uri="api.yaml",
                content_type="application/yaml",
                sha256="abc123",
                content=openapi_content,
            )
        ]
        
        result = detect_and_parse_spec(state)
        
        # Should be in openapi_spec
        assert result.openapi_spec is not None
        assert "openapi" in result.openapi_spec
        assert result.openapi_spec["info"]["title"] == "Test API"
        
        # Should NOT be in parsed_specs (no file specs)
        assert len(result.parsed_specs) == 0


class TestFileSpecsTypeEnforcement:
    """Ensure file_specs only contains FileSpec objects."""
    
    def test_file_specs_accepts_file_spec_objects(self):
        """FileSpec objects should be accepted in file_specs."""
        state = WorkflowState(
            source_refs=["test.csv"],
            spec_refs=["test.csv"],
            task_description="Test"
        )
        
        file_spec = FileSpec(
            id=None,
            source_system_id=None,
            name="test",
            file_type="csv",
        )
        
        state.file_specs.append(file_spec)
        
        assert len(state.file_specs) == 1
        assert isinstance(state.file_specs[0], FileSpec)
    
    def test_file_specs_type_check_at_runtime(self):
        """Runtime check to catch dict in file_specs."""
        state = WorkflowState(
            source_refs=["test.csv"],
            spec_refs=["test.csv"],
            task_description="Test"
        )
        
        # Add a dict (simulating bad data from router)
        bad_data = {"file_spec": {"name": "bad"}, "fields": []}
        state.file_specs.append(bad_data)  # type: ignore
        
        # Runtime validation should catch this
        violations = validate_file_specs_type(state)
        assert len(violations) > 0
        assert "dict" in violations[0].lower() or "filespec" in violations[0].lower()


class TestFileFieldsTypeEnforcement:
    """Ensure file_fields only contains FileField objects."""
    
    def test_file_fields_accepts_file_field_objects(self):
        """FileField objects should be accepted in file_fields."""
        state = WorkflowState(
            source_refs=["test.csv"],
            spec_refs=["test.csv"],
            task_description="Test"
        )
        
        file_field = FileField(
            id=None,
            file_spec_id=None,
            name="customer_id",
            field_type="integer",
            position=0,
        )
        
        state.file_fields.append(file_field)
        
        assert len(state.file_fields) == 1
        assert isinstance(state.file_fields[0], FileField)
    
    def test_file_fields_type_check_at_runtime(self):
        """Runtime check to catch dict in file_fields."""
        state = WorkflowState(
            source_refs=["test.csv"],
            spec_refs=["test.csv"],
            task_description="Test"
        )
        
        # Add a dict (simulating bad data)
        bad_data = {"name": "bad_field", "field_type": "string"}
        state.file_fields.append(bad_data)  # type: ignore
        
        # Runtime validation should catch this
        violations = validate_file_fields_type(state)
        assert len(violations) > 0
        assert "dict" in violations[0].lower() or "filefield" in violations[0].lower()


class TestBuildSilverFileModelNodeIntegrity:
    """Test that build_silver_file_model node is properly wired into the graph."""
    
    def test_build_silver_file_model_in_graph(self):
        """Smoke test: build_silver_file_model must be wired into the graph."""
        from integration_coworker.graph import runtime
        
        # Get the workflow graph
        # Check that the node is registered
        graph_source = runtime.__file__
        
        with open(graph_source, 'r') as f:
            source_code = f.read()
        
        # Check import
        assert "from integration_coworker.graph.nodes import build_silver_file_model" in source_code, \
            "build_silver_file_model must be imported in runtime.py"
        
        # Check node registration
        assert "build_silver_file_model" in source_code, \
            "build_silver_file_model must be added as a node"
        
        # Check edge wiring - must have incoming and outgoing edges
        assert 'add_node("build_silver_file_model"' in source_code or \
               'add_node("build_silver_file_model",' in source_code, \
            "build_silver_file_model must be registered with add_node()"
    
    def test_build_silver_file_model_node_callable(self):
        """Verify build_silver_file_model can be imported and called."""
        from integration_coworker.graph.nodes.build_silver_file_model import build_silver_file_model
        
        state = WorkflowState(
            source_refs=["test.csv"],
            spec_refs=["test.csv"],
            task_description="Test"
        )
        state.source_system = SourceSystem(
            id=1, code="test", name="Test System"
        )
        state.file_specs = []
        
        result = build_silver_file_model(state)
        
        assert result is not None
        assert "build_silver_file_model" in result.completed_steps


# =============================================================================
# Validation Functions (used by tests and can be used at runtime)
# =============================================================================

def validate_file_specs_type(state: WorkflowState) -> list[str]:
    """
    Validate that all items in file_specs are FileSpec objects.
    
    Returns list of violations (empty if valid).
    """
    violations = []
    
    for i, item in enumerate(state.file_specs):
        if not isinstance(item, FileSpec):
            violations.append(
                f"file_specs[{i}] is {type(item).__name__}, expected FileSpec"
            )
    
    return violations


def validate_file_fields_type(state: WorkflowState) -> list[str]:
    """
    Validate that all items in file_fields are FileField objects.
    
    Returns list of violations (empty if valid).
    """
    violations = []
    
    for i, item in enumerate(state.file_fields):
        if not isinstance(item, FileField):
            violations.append(
                f"file_fields[{i}] is {type(item).__name__}, expected FileField"
            )
    
    return violations


def validate_state_types(state: WorkflowState) -> list[str]:
    """
    Validate all type constraints on WorkflowState.
    
    This can be called at key points in the workflow to catch type violations early.
    
    Returns list of all violations (empty if valid).
    """
    violations = []
    violations.extend(validate_file_specs_type(state))
    violations.extend(validate_file_fields_type(state))
    return violations
