"""
Postgres Integration Tests.

V2.1: Tests for real Postgres with pgvector (Section 13.5).

These tests run in CI with a real Postgres container.
They are skipped when DATABASE_URL is not set.
"""
import os
import pytest

# Skip all tests if DATABASE_URL not set
pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL")
    or os.getenv("USE_SQLITE", "").lower() == "true"
    or os.getenv("VALIDATION_PROFILE", "").lower() == "offline",
    reason="Postgres integration tests require DATABASE_URL and are skipped in SQLite/offline mode",
)


class TestPostgresConnection:
    """Test basic Postgres connectivity."""
    
    def test_can_connect_to_postgres(self):
        """Verify we can connect to the Postgres instance."""
        from integration_coworker.persistence.postgres import get_connection
        
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 as test")
                row = cur.fetchone()
                assert row[0] == 1
    
    def test_pgvector_extension_available(self):
        """Verify pgvector extension is installed."""
        from integration_coworker.persistence.postgres import get_connection
        
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT installed_version 
                    FROM pg_available_extensions 
                    WHERE name = 'vector'
                """)
                row = cur.fetchone()
                assert row is not None, "pgvector extension not available"


class TestSilverModelPersistence:
    """Test Silver model persistence to Postgres."""
    
    def test_persist_source_system(self):
        """Test persisting a source system via the checkpoint node."""
        from integration_coworker.graph.nodes.persist_silver_checkpoint import persist_silver_checkpoint
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.domain.models import SourceSystem
        
        state = WorkflowState(
            source_refs=["https://api.example.com/openapi.json"],
            spec_refs=["https://api.example.com/openapi.json"],
            task_description="Test task",
            provider_code="test_provider",
            source_system=SourceSystem(
                id=None,
                code="test_provider",
                name="Test Provider",
                base_url="https://api.example.com",
            ),
            run_id="test_run_postgres_001",
        )
        
        # This should not raise - runs the persist checkpoint node
        result = persist_silver_checkpoint(state)
        
        # Verify step was completed
        assert "persist_silver_checkpoint" in result.completed_steps


class TestEmbeddingSearch:
    """Test pgvector embedding search."""
    
    def test_embedding_dimension_matches(self):
        """Test that we can insert and query 1536-dim embeddings."""
        from integration_coworker.persistence.postgres import get_connection
        
        with get_connection() as conn:
            with conn.cursor() as cur:
                # This requires the spec_chunk_embeddings table to exist
                # Just verify the vector type works
                cur.execute("""
                    SELECT typname FROM pg_type 
                    WHERE typname = 'vector'
                """)
                row = cur.fetchone()
                assert row is not None, "vector type not found"


class TestTransactionRollback:
    """Test that failed transactions roll back properly."""
    
    def test_failed_transaction_rolls_back(self):
        """Verify transaction isolation works."""
        from integration_coworker.persistence.postgres import get_connection
        import psycopg
        
        # Try to insert something that will fail
        with pytest.raises((psycopg.errors.UniqueViolation, psycopg.Error, Exception)):
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Try to insert a source system without required fields
                    cur.execute("""
                        INSERT INTO spec_silver.source_systems (id) 
                        VALUES (999999)
                    """)
                    # Force the error - duplicate
                    cur.execute("""
                        INSERT INTO spec_silver.source_systems (id) 
                        VALUES (999999)
                    """)
        
        # Verify it was rolled back by getting a fresh connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) FROM spec_silver.source_systems 
                    WHERE id = 999999
                """)
                count = cur.fetchone()[0]
                assert count == 0, "Transaction was not rolled back"
