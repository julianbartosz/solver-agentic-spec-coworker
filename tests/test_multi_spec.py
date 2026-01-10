"""
Tests for multi-spec support (Phase 4).

Verifies that the workflow correctly handles multiple spec_refs:
- Primary spec (spec_refs[0]) and supporting specs (spec_refs[1:])
- All specs are ingested and parsed
- Endpoints/schemas from all specs are extracted
- Chunk-to-spec mapping is tracked for persistence
"""
import os
import pytest

from integration_coworker.api.entrypoint import design_and_generate_integration
from integration_coworker.api.types import IntegrationOptions

pytestmark = pytest.mark.requires_aiosqlite


@pytest.fixture
def payments_spec_path() -> str:
    """Path to the mock payments OpenAPI spec."""
    return os.path.join(
        os.path.dirname(__file__),
        "fixtures",
        "mock_payments_openapi.yaml"
    )


@pytest.fixture
def notifications_spec_path() -> str:
    """Path to the mock notifications OpenAPI spec."""
    return os.path.join(
        os.path.dirname(__file__),
        "fixtures",
        "mock_notifications_openapi.yaml"
    )


def test_multi_spec_ingestion(payments_spec_path: str, notifications_spec_path: str):
    """Test that multiple specs are all ingested successfully."""
    options = IntegrationOptions(repo_integration_enabled=False, dry_run=True)
    
    result = design_and_generate_integration(
        spec_refs=[payments_spec_path, notifications_spec_path],
        task_description="Create checkout and notification flow",
        options=options,
    )
    
    # Both specs should be ingested
    assert len(result.spec_documents) == 2
    
    # Verify both spec URIs are present
    spec_uris = {doc.uri for doc in result.spec_documents}
    assert payments_spec_path in spec_uris
    assert notifications_spec_path in spec_uris


def test_multi_spec_endpoints_extraction(payments_spec_path: str, notifications_spec_path: str):
    """Test that endpoints from all specs are extracted."""
    options = IntegrationOptions(repo_integration_enabled=False, dry_run=True)
    
    result = design_and_generate_integration(
        spec_refs=[payments_spec_path, notifications_spec_path],
        task_description="Create checkout and notification flow",
        options=options,
    )
    
    # Should have endpoints from both specs
    # Payments: 2 endpoints (POST /v1/checkout/sessions, GET /v1/checkout/sessions/{id})
    # Notifications: 2 endpoints (POST /v1/notifications, GET /v1/notifications/{id})
    assert len(result.endpoints) == 4
    
    # Verify specific endpoints exist
    endpoint_paths = {(ep.method, ep.path) for ep in result.endpoints}
    assert ("POST", "/v1/checkout/sessions") in endpoint_paths
    assert ("GET", "/v1/checkout/sessions/{id}") in endpoint_paths
    assert ("POST", "/v1/notifications") in endpoint_paths
    assert ("GET", "/v1/notifications/{id}") in endpoint_paths


def test_multi_spec_schemas_extraction(payments_spec_path: str, notifications_spec_path: str):
    """Test that schemas from all specs are extracted."""
    options = IntegrationOptions(repo_integration_enabled=False, dry_run=True)
    
    result = design_and_generate_integration(
        spec_refs=[payments_spec_path, notifications_spec_path],
        task_description="Create checkout and notification flow",
        options=options,
    )
    
    # Should have schemas from both specs
    # Payments: CreateCheckoutSessionRequest, CheckoutSession
    # Notifications: SendNotificationRequest, Notification
    assert len(result.schemas) == 4
    
    schema_names = {s.name for s in result.schemas}
    assert "CreateCheckoutSessionRequest" in schema_names
    assert "CheckoutSession" in schema_names
    assert "SendNotificationRequest" in schema_names
    assert "Notification" in schema_names


def test_multi_spec_provider_code_from_primary(payments_spec_path: str, notifications_spec_path: str):
    """Test that provider_code is inferred from the primary (first) spec."""
    options = IntegrationOptions(repo_integration_enabled=False, dry_run=True)
    
    result = design_and_generate_integration(
        spec_refs=[payments_spec_path, notifications_spec_path],
        task_description="Create checkout and notification flow",
        options=options,
    )
    
    # Provider should be inferred from primary spec (payments)
    # payments is "mockpayments" from the URL
    assert result.provider_code is not None
    assert "payment" in result.provider_code.lower() or "mockpayments" in result.provider_code.lower()


def test_multi_spec_chunk_mapping(payments_spec_path: str, notifications_spec_path: str):
    """Test that chunk-to-spec mapping is created for multi-spec."""
    options = IntegrationOptions(repo_integration_enabled=False, dry_run=True)
    
    result = design_and_generate_integration(
        spec_refs=[payments_spec_path, notifications_spec_path],
        task_description="Create checkout and notification flow",
        options=options,
    )
    
    # Verify chunks exist from both specs
    assert len(result.doc_chunks) > 0
    
    # Verify the plan has chunk mapping
    assert result.plan is not None
    assert "chunk_index_to_spec_document_uri" in result.plan
    
    chunk_mapping = result.plan["chunk_index_to_spec_document_uri"]
    
    # All chunks should be mapped
    assert len(chunk_mapping) == len(result.doc_chunks)
    
    # Mapping should include both spec URIs
    mapped_uris = set(chunk_mapping.values())
    assert payments_spec_path in mapped_uris
    assert notifications_spec_path in mapped_uris


def test_multi_spec_no_errors(payments_spec_path: str, notifications_spec_path: str, mock_embeddings):
    """Test that multi-spec processing completes without fatal errors."""
    options = IntegrationOptions(repo_integration_enabled=False, dry_run=True)
    
    result = design_and_generate_integration(
        spec_refs=[payments_spec_path, notifications_spec_path],
        task_description="Create checkout and notification flow",
        options=options,
    )
    
    # Should complete without fatal errors (warnings are ok)
    # Filter out expected warnings about endpoint_id=None (pre-persistence state)
    fatal_errors = [
        e for e in result.errors 
        if not e.startswith("Warning:") and "stub" not in e.lower()
    ]
    assert not fatal_errors, f"Unexpected fatal errors: {fatal_errors}"
    
    # Key steps should be completed
    assert "plan_run" in result.completed_steps
    assert "ingest_spec" in result.completed_steps
    assert "detect_and_parse_spec" in result.completed_steps
    assert "build_silver_api_model" in result.completed_steps
