"""
Unit tests for build_silver_api_model node.

Validates that the node correctly extracts endpoints, schemas, and fields
from OpenAPI specifications into the Silver layer model.
"""
import pytest
from pathlib import Path
from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.nodes.build_silver_api_model import build_silver_api_model
from integration_coworker.domain.models import SpecDocument


@pytest.fixture
def mock_payments_spec():
    """Load the mock_payments OpenAPI spec."""
    spec_path = Path(__file__).parent / "fixtures" / "mock_payments_openapi.yaml"
    return spec_path


def test_build_silver_api_model_extracts_endpoints(mock_payments_spec):
    """Test that build_silver_api_model extracts endpoints correctly."""
    # Create initial state with parsed spec
    import yaml
    with open(mock_payments_spec, 'r') as f:
        spec_content = f.read()
        spec_dict = yaml.safe_load(spec_content)
    
    state = WorkflowState(
        source_refs=[str(mock_payments_spec)],
        spec_refs=[str(mock_payments_spec)],
        task_description="Create checkout session",
        spec_documents=[
            SpecDocument(
                id=None,
                source_system_id=None,
                version="1.0.0",
                uri=str(mock_payments_spec),
                content_type="application/yaml",
                sha256="test_hash",
                content=spec_content
            )
        ],
        openapi_spec=spec_dict
    )
    
    # Run the node
    result_state = build_silver_api_model(state)
    
    # Verify endpoints were extracted
    assert len(result_state.endpoints) >= 1, "Should extract at least one endpoint"
    
    # Find the checkout endpoint
    checkout_endpoints = [ep for ep in result_state.endpoints if "checkout" in ep.path.lower()]
    assert len(checkout_endpoints) >= 1, "Should extract checkout session endpoint"
    
    # Verify POST endpoint exists
    post_endpoints = [ep for ep in result_state.endpoints if ep.method == "POST"]
    assert len(post_endpoints) >= 1, "Should have at least one POST endpoint"
    
    # Verify endpoint has correct path
    create_session_ep = next((ep for ep in result_state.endpoints if ep.path == "/v1/checkout/sessions"), None)
    assert create_session_ep is not None, "Should find /v1/checkout/sessions endpoint"
    assert create_session_ep.operation_id == "createCheckoutSession"
    assert create_session_ep.summary is not None


def test_build_silver_api_model_extracts_parameters(mock_payments_spec):
    """Test that build_silver_api_model extracts endpoint parameters."""
    # Create initial state with parsed spec
    import yaml
    with open(mock_payments_spec, 'r') as f:
        spec_content = f.read()
        spec_dict = yaml.safe_load(spec_content)
    
    state = WorkflowState(
        source_refs=[str(mock_payments_spec)],
        spec_refs=[str(mock_payments_spec)],
        task_description="Create checkout session",
        spec_documents=[
            SpecDocument(
                id=None,
                source_system_id=None,
                version="1.0.0",
                uri=str(mock_payments_spec),
                content_type="application/yaml",
                sha256="test_hash",
                content=spec_content
            )
        ],
        openapi_spec=spec_dict
    )
    
    # Run the node
    result_state = build_silver_api_model(state)
    
    # Verify parameters were extracted (GET endpoint should have path parameter)
    assert len(result_state.endpoint_parameters) > 0, "Should extract at least one parameter"
    
    # Check for the 'id' path parameter in GET endpoint
    id_params = [p for p in result_state.endpoint_parameters if p.name == "id"]
    assert len(id_params) >= 1, "Should extract 'id' path parameter"
    assert id_params[0].location == "path"
    assert id_params[0].required is True


def test_build_silver_api_model_extracts_schemas(mock_payments_spec):
    """Test that build_silver_api_model extracts schemas and schema fields."""
    # Create initial state with parsed spec
    import yaml
    with open(mock_payments_spec, 'r') as f:
        spec_content = f.read()
        spec_dict = yaml.safe_load(spec_content)
    
    state = WorkflowState(
        source_refs=[str(mock_payments_spec)],
        spec_refs=[str(mock_payments_spec)],
        task_description="Create checkout session",
        spec_documents=[
            SpecDocument(
                id=None,
                source_system_id=None,
                version="1.0.0",
                uri=str(mock_payments_spec),
                content_type="application/yaml",
                sha256="test_hash",
                content=spec_content
            )
        ],
        openapi_spec=spec_dict
    )
    
    # Run the node
    result_state = build_silver_api_model(state)
    
    # Verify schemas were extracted
    assert len(result_state.schemas) >= 1, "Should extract at least one schema"
    
    # Check for specific schemas
    schema_names = [s.name for s in result_state.schemas]
    assert "CreateCheckoutSessionRequest" in schema_names or "CheckoutSession" in schema_names
    
    # Verify schema fields were extracted
    assert len(result_state.schema_fields) > 0, "Should extract schema fields"
    
    # Check for specific fields (amount, currency, etc.)
    field_names = [f.name for f in result_state.schema_fields]
    assert any(field in field_names for field in ["amount", "currency", "session_id", "checkout_url"]), \
        "Should extract key fields from checkout schemas"
    
    # Verify field properties
    amount_field = next((f for f in result_state.schema_fields if f.name == "amount"), None)
    if amount_field:
        assert amount_field.type == "integer"
        assert amount_field.json_path == "$.amount"


def test_build_silver_api_model_no_errors(mock_payments_spec):
    """Test that build_silver_api_model completes without errors."""
    # Create initial state with parsed spec
    import yaml
    with open(mock_payments_spec, 'r') as f:
        spec_content = f.read()
        spec_dict = yaml.safe_load(spec_content)
    
    state = WorkflowState(
        source_refs=[str(mock_payments_spec)],
        spec_refs=[str(mock_payments_spec)],
        task_description="Create checkout session",
        spec_documents=[
            SpecDocument(
                id=None,
                source_system_id=None,
                version="1.0.0",
                uri=str(mock_payments_spec),
                content_type="application/yaml",
                sha256="test_hash",
                content=spec_content
            )
        ],
        openapi_spec=spec_dict
    )
    
    # Run the node
    result_state = build_silver_api_model(state)
    
    # Verify no errors were added
    assert len(result_state.errors) == 0, f"Should complete without errors, but got: {result_state.errors}"
    
    # Verify the step was completed
    assert "build_silver_api_model" in result_state.completed_steps
