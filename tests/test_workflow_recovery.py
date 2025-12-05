"""
Tests for workflow checkpointing and recovery.

Per V2 Implementation Plan Section 3.5:
- Checkpoint persistence after each node
- Resume capability from checkpoints
- True skip with dependency analysis
"""
import pytest
from datetime import datetime, timezone

from integration_coworker.graph.state import WorkflowState
from integration_coworker.persistence.db import init_schema, clear_test_data, get_engine_type
from integration_coworker.persistence.checkpoints import (
    save_checkpoint,
    load_checkpoint,
    get_completed_nodes,
    delete_checkpoints,
    _serialize_state,
    _deserialize_state,
)
from integration_coworker.api.recovery import (
    RecoveryContext,
    create_recovery_context,
    _state_to_result,
)
from integration_coworker.graph.runtime import get_node_names, WORKFLOW_NODE_ORDER


def _create_test_state(**kwargs) -> WorkflowState:
    """Create a WorkflowState with required fields for testing."""
    defaults = {
        "source_refs": ["https://example.com/api.yaml"],
        "spec_refs": ["https://example.com/api.yaml"],
        "task_description": "Test task",
    }
    defaults.update(kwargs)
    return WorkflowState(**defaults)


@pytest.fixture(autouse=True)
def setup_db():
    """Initialize DB schema before each test."""
    init_schema()
    clear_test_data()
    yield
    clear_test_data()


class TestCheckpointSerialization:
    """Tests for state serialization/deserialization."""
    
    def test_serialize_minimal_state(self):
        """Minimal state should serialize to JSON-compatible dict."""
        state = _create_test_state()
        state.run_id = "test-run-123"
        state.provider_code = "test_provider"
        
        serialized = _serialize_state(state)
        
        assert serialized["run_id"] == "test-run-123"
        assert serialized["provider_code"] == "test_provider"
        assert isinstance(serialized, dict)
    
    def test_serialize_with_completed_steps(self):
        """Completed steps list should serialize correctly."""
        state = _create_test_state()
        state.run_id = "test-run-456"
        state.completed_steps = ["plan_run", "ingest_spec"]
        
        serialized = _serialize_state(state)
        
        assert serialized["completed_steps"] == ["plan_run", "ingest_spec"]
    
    def test_deserialize_basic_fields(self):
        """Basic fields should deserialize correctly."""
        data = {
            "source_refs": ["https://example.com/api.yaml"],
            "spec_refs": ["https://example.com/api.yaml"],
            "run_id": "test-run-789",
            "provider_code": "stripe",
            "task_description": "Process payments",
            "completed_steps": ["plan_run"],
            "errors": [],
            "warnings": ["minor warning"],
            "plan": {"use_repo": False},
        }
        
        state = _deserialize_state(data)
        
        assert state.run_id == "test-run-789"
        assert state.provider_code == "stripe"
        assert state.task_description == "Process payments"
        assert state.completed_steps == ["plan_run"]
        assert state.warnings == ["minor warning"]
        assert state.plan == {"use_repo": False}
    
    def test_large_doc_chunks_truncated(self):
        """Large doc_chunks list should be truncated to prevent DB bloat."""
        state = _create_test_state()
        state.run_id = "test-run-truncate"
        state.doc_chunks = ["chunk"] * 200  # More than 100
        
        serialized = _serialize_state(state)
        
        assert len(serialized["doc_chunks"]) == 100
        assert serialized.get("_truncated", {}).get("doc_chunks") is True


class TestCheckpointPersistence:
    """Tests for checkpoint database operations."""
    
    def test_save_and_load_checkpoint(self):
        """Save a checkpoint and load it back."""
        # First ensure run_status exists (FK dependency)
        _create_run_status("test-run-save")
        
        state = _create_test_state()
        state.run_id = "test-run-save"
        state.provider_code = "mock_payments"
        state.completed_steps = ["plan_run", "ingest_spec"]
        
        save_checkpoint("test-run-save", "ingest_spec", state)
        
        loaded = load_checkpoint("test-run-save", "ingest_spec")
        
        assert loaded is not None
        assert loaded.run_id == "test-run-save"
        assert loaded.provider_code == "mock_payments"
        assert loaded.completed_steps == ["plan_run", "ingest_spec"]
    
    def test_load_latest_checkpoint(self):
        """Loading without node_name should return the latest checkpoint."""
        _create_run_status("test-run-latest")
        
        state1 = _create_test_state()
        state1.run_id = "test-run-latest"
        state1.completed_steps = ["plan_run"]
        save_checkpoint("test-run-latest", "plan_run", state1)
        
        state2 = _create_test_state()
        state2.run_id = "test-run-latest"
        state2.completed_steps = ["plan_run", "ingest_spec"]
        save_checkpoint("test-run-latest", "ingest_spec", state2)
        
        latest = load_checkpoint("test-run-latest")
        
        assert latest is not None
        assert "ingest_spec" in latest.completed_steps
    
    def test_load_nonexistent_checkpoint_returns_none(self):
        """Loading a non-existent checkpoint should return None."""
        loaded = load_checkpoint("nonexistent-run-id")
        assert loaded is None
    
    def test_get_completed_nodes(self):
        """Get list of completed nodes for a run."""
        _create_run_status("test-run-completed")
        
        state = _create_test_state()
        state.run_id = "test-run-completed"
        
        save_checkpoint("test-run-completed", "plan_run", state)
        save_checkpoint("test-run-completed", "ingest_spec", state)
        save_checkpoint("test-run-completed", "detect_and_parse_spec", state)
        
        nodes = get_completed_nodes("test-run-completed")
        
        assert len(nodes) == 3
        assert "plan_run" in nodes
        assert "ingest_spec" in nodes
        assert "detect_and_parse_spec" in nodes
    
    def test_delete_checkpoints(self):
        """Delete all checkpoints for a run."""
        _create_run_status("test-run-delete")
        
        state = _create_test_state()
        state.run_id = "test-run-delete"
        
        save_checkpoint("test-run-delete", "plan_run", state)
        save_checkpoint("test-run-delete", "ingest_spec", state)
        
        delete_checkpoints("test-run-delete")
        
        nodes = get_completed_nodes("test-run-delete")
        assert len(nodes) == 0
    
    def test_upsert_overwrites_existing(self):
        """Saving checkpoint for same node should update, not insert."""
        _create_run_status("test-run-upsert")
        
        state1 = _create_test_state()
        state1.run_id = "test-run-upsert"
        state1.completed_steps = ["step1"]
        save_checkpoint("test-run-upsert", "plan_run", state1)
        
        state2 = _create_test_state()
        state2.run_id = "test-run-upsert"
        state2.completed_steps = ["step1", "step2"]
        save_checkpoint("test-run-upsert", "plan_run", state2)
        
        loaded = load_checkpoint("test-run-upsert", "plan_run")
        
        assert loaded is not None
        assert loaded.completed_steps == ["step1", "step2"]
        
        # Should still be only one checkpoint for plan_run
        nodes = get_completed_nodes("test-run-upsert")
        assert nodes.count("plan_run") == 1


class TestRecoveryContext:
    """Tests for recovery context creation."""
    
    def test_create_recovery_context_minimal(self):
        """Create context with minimal inputs."""
        inputs = {
            "spec_refs": ["https://example.com/api.yaml"],
            "task_description": "List users",
        }
        
        ctx = create_recovery_context(inputs, run_id="run-123")
        
        assert ctx.run_id == "run-123"
        assert ctx.spec_refs == ["https://example.com/api.yaml"]
        assert ctx.task_description == "List users"
        assert ctx.provider_code is None
        assert ctx.dry_run is True
    
    def test_create_recovery_context_full(self):
        """Create context with all inputs."""
        inputs = {
            "spec_refs": ["spec.yaml"],
            "task_description": "Process payment",
            "provider_code": "stripe",
            "repo_root": "/path/to/repo",
            "dry_run": False,
        }
        
        ctx = create_recovery_context(
            inputs,
            error="LLM timeout",
            failed_step="generate_code_and_tests",
            run_id="run-456",
        )
        
        assert ctx.run_id == "run-456"
        assert ctx.provider_code == "stripe"
        assert ctx.repo_root == "/path/to/repo"
        assert ctx.dry_run is False
        assert ctx.last_error == "LLM timeout"
        assert ctx.failed_step == "generate_code_and_tests"


class TestWorkflowNodeOrder:
    """Tests for workflow node ordering."""
    
    def test_get_node_names_returns_copy(self):
        """get_node_names should return a copy, not the original list."""
        nodes = get_node_names()
        original_len = len(WORKFLOW_NODE_ORDER)
        
        nodes.append("fake_node")
        
        assert len(WORKFLOW_NODE_ORDER) == original_len
    
    def test_node_order_includes_key_nodes(self):
        """Workflow should include key nodes in order."""
        nodes = get_node_names()
        
        # Check key nodes exist
        assert "plan_run" in nodes
        assert "ingest_spec" in nodes
        assert "understand_task" in nodes
        assert "generate_code_and_tests" in nodes
        assert "persist_run_outcome" in nodes
        
        # Check order
        assert nodes.index("plan_run") < nodes.index("ingest_spec")
        assert nodes.index("understand_task") < nodes.index("generate_code_and_tests")
        assert nodes.index("build_report") < nodes.index("persist_run_outcome")


class TestStateToResult:
    """Tests for state to result conversion."""
    
    def test_successful_state_to_result(self):
        """State should convert to IntegrationResult correctly."""
        state = _create_test_state()
        state.run_id = "success-run"
        state.provider_code = "mock_payments"
        state.errors = []
        state.warnings = []
        
        result = _state_to_result(state)
        
        assert result.run_id == "success-run"
        assert result.provider_code == "mock_payments"
        assert result.errors == []
    
    def test_failed_state_to_result(self):
        """State with errors should include them in result."""
        state = _create_test_state()
        state.run_id = "failed-run"
        state.errors = ["LLM call failed"]
        
        result = _state_to_result(state)
        
        assert result.run_id == "failed-run"
        assert result.errors == ["LLM call failed"]


def _create_run_status(run_id: str):
    """Helper to create a run_status record for FK dependencies."""
    engine = get_engine_type()
    
    if engine == "postgres":
        from integration_coworker.persistence.postgres import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO integration_gold.run_status (run_id, status)
                    VALUES (%s, 'running')
                    ON CONFLICT (run_id) DO NOTHING
                """, (run_id,))
            conn.commit()
    else:
        from integration_coworker.persistence.db import get_sqlite_connection
        conn = get_sqlite_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO run_status (run_id, status)
            VALUES (?, 'running')
        """, (run_id,))
        conn.commit()
        conn.close()
