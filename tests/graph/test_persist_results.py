"""
Tests for persist_results node.

Verifies:
- Dry-run mode: no DB writes, summary in persisted_ids
- Normal mode: records inserted, IDs backfilled
"""
import pytest
from pathlib import Path
from integration_coworker.graph.nodes.persist_results import persist_results
from integration_coworker.graph.state import WorkflowState
from integration_coworker.api.types import IntegrationOptions
from integration_coworker.domain.models import (
    SpecDocument, Endpoint, Schema, Entity, IntegrationTask,
    IntegrationFlowNode, IntegrationFlowEdge, EndpointBinding
)
from integration_coworker.persistence import db


def test_dry_run_no_db_writes():
    """Test that dry_run=True prevents database writes."""
    state = WorkflowState(
        source_refs=[],
        spec_refs=["test.yaml"],
        task_description="Test task",
        provider_code="test_provider",
        options=IntegrationOptions(dry_run=True),
    )
    
    # Add some drafts
    state.spec_documents.append(
        SpecDocument(id=None, source_system_id=None, version="1.0",
                    uri="test.yaml", content_type="yaml", sha256="abc123", content="test")
    )
    state.endpoints.append(
        Endpoint(id=None, source_system_id=None, spec_document_id=None,
                path="/test", method="GET", operation_id="test_op",
                summary="Test", description=None, request_schema_id=None,
                response_schema_id=None, auth_required=False)
    )
    
    result = persist_results(state)
    
    # Should not write to DB
    assert result.persisted_ids is not None
    assert result.persisted_ids["dry_run"] is True
    assert result.persisted_ids["run_status"] == "completed_dry_run"
    assert "would_persist" in result.persisted_ids
    
    # Verify no DB rows created
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM source_systems")
    assert cur.fetchone()[0] == 0


def test_normal_mode_writes_to_db():
    """Test that normal mode writes to database and backfills IDs."""
    state = WorkflowState(
        source_refs=[],
        spec_refs=["test.yaml"],
        task_description="Test task",
        provider_code="test_provider",
        options=IntegrationOptions(dry_run=False),
    )
    
    # Add drafts
    spec_doc = SpecDocument(
        id=None, source_system_id=None, version="1.0",
        uri="test.yaml", content_type="yaml", sha256="abc123", content="test"
    )
    state.spec_documents.append(spec_doc)
    
    endpoint = Endpoint(
        id=None, source_system_id=None, spec_document_id=None,
        path="/test", method="GET", operation_id="test_op",
        summary="Test", description=None, request_schema_id=None,
        response_schema_id=None, auth_required=False
    )
    state.endpoints.append(endpoint)
    
    task = IntegrationTask(
        id=None, source_system_id=None, task_slug="test_task",
        provider_code="test_provider", description="Test"
    )
    state.integration_task = task
    
    result = persist_results(state)
    
    # Should have persisted_ids
    assert result.persisted_ids is not None
    assert result.persisted_ids["run_status"] in ("completed", "completed_with_errors")
    assert result.persisted_ids["source_system_id"] is not None
    assert result.persisted_ids["spec_document_id"] is not None
    assert result.persisted_ids["task_id"] is not None
    
    # Verify IDs backfilled
    assert spec_doc.id is not None
    assert endpoint.id is not None
    assert task.id is not None
    
    # Verify DB rows exist
    conn = db.get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT COUNT(*) FROM source_systems WHERE code = ?", ("test_provider",))
    assert cur.fetchone()[0] == 1
    
    cur.execute("SELECT COUNT(*) FROM spec_documents")
    assert cur.fetchone()[0] == 1
    
    cur.execute("SELECT COUNT(*) FROM endpoints")
    assert cur.fetchone()[0] == 1


def test_idempotent_persistence():
    """Test that running persist_results twice doesn't create duplicates."""
    state = WorkflowState(
        source_refs=[],
        spec_refs=["test.yaml"],
        task_description="Test task",
        provider_code="test_provider",
        options=IntegrationOptions(dry_run=False),
    )
    
    state.spec_documents.append(
        SpecDocument(id=None, source_system_id=None, version="1.0",
                    uri="test.yaml", content_type="yaml", sha256="same_hash", content="test")
    )
    
    # Run twice
    persist_results(state)
    persist_results(state)
    
    # Should still have only one row
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM spec_documents")
    assert cur.fetchone()[0] == 1


def test_completed_steps_appended():
    """Test that persist_results appends to completed_steps."""
    state = WorkflowState(
        source_refs=[],
        spec_refs=["test.yaml"],
        task_description="Test",
        options=IntegrationOptions(dry_run=True),
    )
    
    assert "persist_results" not in state.completed_steps
    
    persist_results(state)
    
    assert "persist_results" in state.completed_steps
