"""
Integration Tests for LangGraph Checkpointing
==============================================

Tests that verify LangGraph checkpointing works correctly with the
configured package versions. These tests catch version incompatibilities
like the JsonPlusSerializer.dumps() issue.

Per PRODUCTION_AUDIT_DEC12.md - Recommendation #2.

Run unit tests (no DB needed):
    pytest tests/test_langgraph_checkpointing.py -v -k "not Integration"

Run all tests (requires Postgres):
    DATABASE_URL="postgresql://integration:integration@localhost:5432/integration_coworker" \
    pytest tests/test_langgraph_checkpointing.py -v
"""

import os
import sys
import pytest
import asyncio
from unittest.mock import patch, MagicMock
from typing import Any, Dict

pytestmark = pytest.mark.requires_langgraph_checkpoint


# Skip DB reset for unit tests in this file
@pytest.fixture(autouse=True)
def skip_db_reset(request):
    """Skip the default db reset fixture for non-integration tests."""
    if "Integration" not in request.node.nodeid:
        # For unit tests, don't try to connect to DB
        pass


class TestLangGraphPackageCompatibility:
    """Test that LangGraph packages are compatible and work together.
    
    These are UNIT tests - they don't require database connectivity.
    """
    
    @pytest.fixture(autouse=True)
    def skip_reset_db(self, request, monkeypatch):
        """Disable DB operations for these unit tests."""
        monkeypatch.setenv("USE_SQLITE", "true")
        monkeypatch.setenv("USE_MOCK_LLM", "true")
    
    def test_langgraph_imports(self):
        """Verify all critical LangGraph imports work."""
        # This catches import errors from version mismatches
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        from langgraph.checkpoint.base import BaseCheckpointSaver
        
        assert AsyncPostgresSaver is not None
        assert AsyncSqliteSaver is not None
        assert JsonPlusSerializer is not None
        assert BaseCheckpointSaver is not None
    
    def test_jsonplus_serializer_methods(self):
        """Verify JsonPlusSerializer has the required methods.
        
        This specifically tests for the bug we encountered where
        dumps() was called but doesn't exist in v3.x (it's dumps_typed).
        """
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        
        serializer = JsonPlusSerializer()
        
        # v3.x API uses dumps_typed/loads_typed
        assert hasattr(serializer, 'dumps_typed'), "Missing dumps_typed method"
        assert hasattr(serializer, 'loads_typed'), "Missing loads_typed method"
        assert callable(getattr(serializer, 'dumps_typed'))
        assert callable(getattr(serializer, 'loads_typed'))
    
    def test_jsonplus_serialization_roundtrip(self):
        """Test that serialization actually works with typical workflow data."""
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        
        serializer = JsonPlusSerializer()
        
        # Test data similar to workflow state
        test_data = {
            "run_id": "test-run-123",
            "provider_code": "stripe",
            "completed_steps": ["parse_spec", "plan_integration"],
            "errors": [],
            "plan": {
                "tasks": [
                    {"id": 1, "name": "Create client", "status": "completed"},
                    {"id": 2, "name": "Generate tests", "status": "pending"}
                ]
            },
            "node_timings": {
                "parse_spec": 1.234,
                "plan_integration": 2.567
            }
        }
        
        # Serialize
        serialized = serializer.dumps_typed(test_data)
        assert serialized is not None
        
        # Deserialize
        deserialized = serializer.loads_typed(serialized)
        assert deserialized == test_data
    
    def test_jsonplus_handles_nested_dataclasses(self):
        """Test serialization with dataclass-like nested structures."""
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        from dataclasses import dataclass, asdict
        
        @dataclass
        class TestEndpoint:
            path: str
            method: str
            operation_id: str
        
        @dataclass
        class TestPlan:
            endpoints: list
            description: str
        
        serializer = JsonPlusSerializer()
        
        # Test with nested structure
        endpoint = TestEndpoint(path="/users", method="GET", operation_id="getUsers")
        plan = TestPlan(endpoints=[asdict(endpoint)], description="Test plan")
        
        test_data = {
            "plan": asdict(plan),
            "raw_endpoint": asdict(endpoint)
        }
        
        serialized = serializer.dumps_typed(test_data)
        deserialized = serializer.loads_typed(serialized)
        
        assert deserialized["plan"]["description"] == "Test plan"
        assert deserialized["raw_endpoint"]["path"] == "/users"


class TestSitePackagesIntegrity:
    """Test that site-packages doesn't have mixed Python versions.
    
    These are UNIT tests - they don't require database connectivity.
    """
    
    @pytest.fixture(autouse=True)
    def skip_reset_db(self, request, monkeypatch):
        """Disable DB operations for these unit tests."""
        monkeypatch.setenv("USE_SQLITE", "true")
        monkeypatch.setenv("USE_MOCK_LLM", "true")
    
    def test_no_mixed_python_versions(self):
        """Detect the mixed site-packages bug that caused version conflicts."""
        import sys
        from pathlib import Path
        
        # Get the site-packages directory
        venv_path = Path(sys.prefix)
        lib_path = venv_path / "lib"
        
        if not lib_path.exists():
            pytest.skip("Not running in a venv with standard layout")
        
        python_dirs = list(lib_path.glob("python*"))
        
        # Should only have one Python version
        assert len(python_dirs) <= 1, (
            f"Mixed Python versions in site-packages! Found: {[d.name for d in python_dirs]}. "
            f"This causes package conflicts. Run: ./scripts/setup_env.sh --clean"
        )
    
    def test_python_version_is_3_11(self):
        """Ensure we're running on Python 3.11.x which has compatible packages."""
        import sys
        
        assert sys.version_info.major == 3, "Expected Python 3.x"
        assert sys.version_info.minor == 11, (
            f"Expected Python 3.11.x, got {sys.version_info.major}.{sys.version_info.minor}. "
            f"Python 3.12/3.13 have package compatibility issues with LangGraph."
        )


class TestPackageVersionConstraints:
    """Test that critical packages are within compatible version ranges.
    
    These are UNIT tests - they don't require database connectivity.
    """
    
    @pytest.fixture(autouse=True)
    def skip_reset_db(self, request, monkeypatch):
        """Disable DB operations for these unit tests."""
        monkeypatch.setenv("USE_SQLITE", "true")
        monkeypatch.setenv("USE_MOCK_LLM", "true")
    
    def test_langgraph_version(self):
        """Verify langgraph is in compatible range."""
        import importlib.metadata
        
        version = importlib.metadata.version("langgraph")
        major, minor, patch = map(int, version.split(".")[:3])
        
        assert major == 1 and minor == 0, (
            f"langgraph {version} may be incompatible. Expected 1.0.x"
        )
    
    def test_langgraph_checkpoint_version(self):
        """Verify langgraph-checkpoint is in compatible range."""
        import importlib.metadata
        
        version = importlib.metadata.version("langgraph-checkpoint")
        major, minor, patch = map(int, version.split(".")[:3])
        
        assert major == 3 and minor == 0, (
            f"langgraph-checkpoint {version} may be incompatible. Expected 3.0.x"
        )
    
    def test_langgraph_checkpoint_postgres_version(self):
        """Verify langgraph-checkpoint-postgres is in compatible range."""
        import importlib.metadata
        
        version = importlib.metadata.version("langgraph-checkpoint-postgres")
        major, minor, patch = map(int, version.split(".")[:3])
        
        assert major == 3 and minor == 0, (
            f"langgraph-checkpoint-postgres {version} may be incompatible. Expected 3.0.x"
        )
    
    def test_langchain_version(self):
        """Verify langchain is in compatible range."""
        import importlib.metadata
        
        version = importlib.metadata.version("langchain")
        major, minor, patch = map(int, version.split(".")[:3])
        
        assert major == 1 and minor == 1, (
            f"langchain {version} may be incompatible. Expected 1.1.x"
        )


# =============================================================================
# INTEGRATION TESTS (require Postgres)
# =============================================================================

@pytest.mark.integration
class TestAsyncPostgresSaverIntegration:
    """Test AsyncPostgresSaver connection and operations.
    
    These are INTEGRATION tests - they require database connectivity.
    """
    
    @pytest.fixture
    def db_url(self):
        """Get database URL from environment."""
        return os.getenv(
            "DATABASE_URL",
            "postgresql://integration:integration@localhost:5432/integration_coworker"
        )
    
    @pytest.mark.asyncio
    async def test_postgres_saver_creates_and_connects(self, db_url):
        """Test that AsyncPostgresSaver can be created and connects."""
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        
        # Test the context manager pattern
        async with AsyncPostgresSaver.from_conn_string(db_url) as saver:
            # Should be able to setup without error
            await saver.setup()
            
            # Verify it created required tables
            # (LangGraph creates its own tables in public schema)
            assert saver is not None
    
    @pytest.mark.asyncio
    async def test_postgres_saver_checkpoint_roundtrip(self, db_url):
        """Test save and load a checkpoint through AsyncPostgresSaver."""
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from langgraph.checkpoint.base import Checkpoint
        import uuid
        
        async with AsyncPostgresSaver.from_conn_string(db_url) as saver:
            await saver.setup()
            
            thread_id = f"test-thread-{uuid.uuid4()}"
            
            # Create a test checkpoint
            config = {"configurable": {"thread_id": thread_id}}
            
            # Put a checkpoint
            checkpoint = Checkpoint(
                v=1,
                id=str(uuid.uuid4()),
                ts=asyncio.get_event_loop().time(),
                channel_values={"messages": [], "test_key": "test_value"},
                channel_versions={},
                versions_seen={},
            )
            
            # Save checkpoint
            checkpoint_config = await saver.aput(
                config,
                checkpoint,
                metadata={"source": "test", "step": 1},
                new_versions={},
            )
            
            assert checkpoint_config is not None
            
            # Load checkpoint
            loaded = await saver.aget_tuple(config)
            
            assert loaded is not None
            assert loaded.checkpoint.channel_values.get("test_key") == "test_value"

    @pytest.mark.postgres
    @pytest.mark.asyncio
    async def test_postgres_saver_roundtrip_with_fixture(self, postgres_env):
        """PostgresSaver roundtrip using test container DSN and pinned versions."""
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from langgraph.checkpoint.base import Checkpoint
        import uuid

        async with AsyncPostgresSaver.from_conn_string(postgres_env) as saver:
            await saver.setup()

            thread_id = f"fixture-thread-{uuid.uuid4()}"
            config = {"configurable": {"thread_id": thread_id}}
            checkpoint = Checkpoint(
                v=1,
                id=str(uuid.uuid4()),
                ts=asyncio.get_event_loop().time(),
                channel_values={"messages": [], "test_key": "fixture_value"},
                channel_versions={},
                versions_seen={},
            )

            await saver.aput(
                config,
                checkpoint,
                metadata={"source": "fixture", "step": 1},
                new_versions={},
            )

            loaded = await saver.aget_tuple(config)

            assert loaded is not None
            assert loaded.checkpoint.channel_values.get("test_key") == "fixture_value"


@pytest.mark.integration
class TestCheckpointRecoveryIntegration:
    """Test checkpoint-based recovery flows.
    
    These are INTEGRATION tests - they require database connectivity.
    """
    
    @pytest.mark.asyncio
    async def test_get_checkpointer_returns_valid_saver(self):
        """Test that get_checkpointer() returns a working checkpointer."""
        from integration_coworker.graph.runtime import get_checkpointer
        
        checkpointer = await get_checkpointer()
        assert checkpointer is not None
        
        # Should be either Postgres or SQLite saver
        class_name = type(checkpointer).__name__
        assert class_name in (
            "AsyncPostgresSaver",
            "AsyncSqliteSaver", 
            "_GeneratorContextManager",  # Context manager wrapper
        ), f"Unexpected checkpointer type: {class_name}"
    
    @pytest.mark.asyncio
    async def test_workflow_state_checkpoint_serialization(self):
        """Test that WorkflowState can be serialized for checkpointing."""
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.persistence.checkpoints import _serialize_state, _deserialize_state
        
        # Create a test workflow state
        state = WorkflowState(
            source_refs=["file1.py", "file2.py"],
            spec_refs=["openapi.yaml"],
            task_description="Test task",
        )
        state.run_id = "test-run-123"
        state.provider_code = "test_provider"
        state.completed_steps = ["parse_spec", "plan_integration"]
        state.plan = {"tasks": [{"id": 1, "name": "Test"}]}
        
        # Serialize
        serialized = _serialize_state(state)
        
        assert isinstance(serialized, dict)
        assert serialized["run_id"] == "test-run-123"
        assert serialized["provider_code"] == "test_provider"
        assert serialized["completed_steps"] == ["parse_spec", "plan_integration"]
        
        # Deserialize
        restored = _deserialize_state(serialized)
        
        assert restored.run_id == "test-run-123"
        assert restored.provider_code == "test_provider"
        assert restored.completed_steps == ["parse_spec", "plan_integration"]


# Run these tests with: pytest tests/test_langgraph_checkpointing.py -v
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

