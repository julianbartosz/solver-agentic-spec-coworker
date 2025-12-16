"""
Pytest configuration and shared fixtures for integration_coworker tests.
"""
import hashlib
import os
import pytest
import sqlite3
import importlib.util
from typing import List, Optional
from unittest.mock import MagicMock

from integration_coworker.persistence import db


# =============================================================================
# Pytest Markers
# =============================================================================

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "postgres: marks tests as requiring Postgres (deselect with '-m \"not postgres\"')"
    )
    config.addinivalue_line(
        "markers", "no_db: marks tests that don't need database reset"
    )


OPTIONAL_DEP_MARKERS = {
    "requires_aiosqlite": ("aiosqlite",),
    "requires_openpyxl": ("openpyxl",),
    "requires_graphql": ("graphql",),
    "requires_reportlab": ("reportlab",),
    "requires_langgraph_checkpoint": (
        "langgraph.checkpoint.sqlite",
        "langgraph.checkpoint.postgres",
    ),
    "requires_simpleeval": ("simpleeval",),
}


def pytest_runtest_setup(item):
    """Skip tests gracefully when optional dependencies are missing."""
    for marker_name, module_names in OPTIONAL_DEP_MARKERS.items():
        if item.get_closest_marker(marker_name):
            for module_name in module_names:
                if importlib.util.find_spec(module_name) is None:
                    pytest.skip(
                        f"Missing optional dependency '{module_name}'. Install with `pip install -e .[test]` to run these tests."
                    )


# =============================================================================
# Fake Embedding Generation (for tests only)
# Per V2 Implementation Plan Section 3.6
# =============================================================================

def _generate_fake_embedding(text: str, dimensions: int = 1536) -> List[float]:
    """
    Generate deterministic fake embedding from text hash.
    
    Uses MD5 hash to create reproducible embeddings for testing.
    """
    hash_bytes = hashlib.md5(text.encode()).digest()
    # Convert 16 bytes to floats and repeat to fill dimensions
    base = [b / 255.0 for b in hash_bytes]
    # Repeat pattern to reach desired dimensions (16 * 96 = 1536)
    return (base * (dimensions // len(base) + 1))[:dimensions]


def _generate_fake_embeddings_batch(texts: List[str], dimensions: int = 1536) -> List[List[float]]:
    """Generate fake embeddings for a batch of texts."""
    return [_generate_fake_embedding(t, dimensions) for t in texts]


@pytest.fixture
def mock_embeddings(monkeypatch):
    """
    Fixture that provides fake embeddings for tests.
    
    Per V2 Implementation Plan Section 3.6:
    All embedding calls will return deterministic fakes based on text hash.
    
    Usage:
        def test_something(mock_embeddings):
            # All embedding calls will return deterministic fakes
            ...
    """
    # Create a mock OpenAI client
    mock_client = MagicMock()
    
    def mock_batch_embed(client, texts: List[str], model: str = None) -> List[Optional[List[float]]]:
        """Mock batch embedding function."""
        return _generate_fake_embeddings_batch(texts)
    
    # Patch the embedding client getter to return our mock
    def mock_get_client():
        return mock_client
    
    monkeypatch.setattr(
        "integration_coworker.graph.nodes.embed_spec_chunks._get_embedding_client",
        mock_get_client
    )
    
    # Patch the batch embed function
    monkeypatch.setattr(
        "integration_coworker.graph.nodes.embed_spec_chunks._batch_embed",
        mock_batch_embed
    )
    
    # Also patch semantic search embedding computation if it exists
    try:
        monkeypatch.setattr(
            "integration_coworker.retrieval.semantic_search.compute_embedding",
            lambda text: _generate_fake_embedding(text)
        )
    except AttributeError:
        pass
    
    return {
        "client": mock_client,
        "generate_single": _generate_fake_embedding,
        "generate_batch": _generate_fake_embeddings_batch,
    }


@pytest.fixture
def mock_llm(monkeypatch):
    """
    Fixture that provides mock LLM responses for tests.
    
    Usage:
        def test_something(mock_llm):
            mock_llm.set_response("expected output")
            # LLM calls will return the set response
            ...
    """
    class MockLLMController:
        def __init__(self):
            self._responses = []
            self._default_response = '{"action": "test", "resource": "test"}'
        
        def set_response(self, response: str):
            """Set the response for the next LLM call."""
            self._responses = [response]
        
        def set_responses(self, responses: List[str]):
            """Set multiple responses for sequential LLM calls."""
            self._responses = list(responses)
        
        def get_response(self) -> str:
            if self._responses:
                return self._responses.pop(0)
            return self._default_response
    
    controller = MockLLMController()
    
    # Patch call_llm_for_node (the current LLM function)
    def mock_call_llm(*args, **kwargs):
        return controller.get_response()
    
    try:
        monkeypatch.setattr(
            "integration_coworker.llm.client.call_llm_for_node",
            mock_call_llm
        )
    except AttributeError:
        pass
    
    return controller


# =============================================================================
# Database Fixtures
# =============================================================================

@pytest.fixture(scope="function", autouse=True)
def reset_db(request):
    """
    Reset database before each test to ensure clean state.
    
    This fixture runs automatically before every test function,
    unless the test is marked with @pytest.mark.no_db.
    Prevents database locking by ensuring clean setup/teardown.
    """
    # Skip database setup for tests marked with no_db
    if "no_db" in [marker.name for marker in request.node.iter_markers()]:
        yield
        return
    
    # Close any lingering connections
    try:
        conn = db.get_connection()
        conn.close()
    except Exception:
        pass
    
    # Initialize schema (idempotent)
    db.init_schema()
    
    # Clear any existing test data
    try:
        db.clear_test_data()
    except sqlite3.OperationalError:
        # If DB is locked, try again after a moment
        import time
        time.sleep(0.1)
        try:
            db.clear_test_data()
        except Exception:
            pass
    
    # Run the test
    yield
    
    # Clean up after test - ensure no connections left open
    try:
        db.clear_test_data()
    except Exception:
        # If cleanup fails, it's not critical
        pass


# =============================================================================
# Testcontainers PostgreSQL Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def postgres_container():
    """
    Session-scoped Postgres container via testcontainers.
    
    Starts a real Postgres instance in Docker for integration testing.
    The container is reused across all tests in the session for speed.
    """
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not installed: pip install testcontainers[postgresql]")
    
    # Check Docker is available
    import subprocess
    result = subprocess.run(["docker", "info"], capture_output=True)
    if result.returncode != 0:
        pytest.skip("Docker is not running or not available")
    
    # Start Postgres container with pgvector extension
    # Use postgres:16 which has better extension support
    # IMPORTANT: driver=None returns driverless URL (postgresql://...)
    # Per testcontainers docs: https://testcontainers-python.readthedocs.io/en/latest/modules/postgres/README.html
    # "To get a URL without a driver, pass in driver=None"
    container = PostgresContainer(
        image="pgvector/pgvector:pg16",
        username="test_user",
        password="test_pass",
        dbname="test_db",
        driver=None,  # Return plain libpq URL, not SQLAlchemy format
    )
    
    with container as pg:
        yield pg


@pytest.fixture(scope="session")
def postgres_dsn(postgres_container):
    """
    Return the DSN for the testcontainers Postgres instance.
    
    Format: postgresql://user:pass@host:port/dbname
    
    IMPORTANT: We build the DSN from container properties directly rather than
    using get_connection_url() because:
    
    1. get_connection_url() is documented as "SQLAlchemy-compatible" (PyPI docs)
    2. psycopg (v2 and v3) requires plain libpq DSN strings
    3. Building from host/port/user/pass ensures correct format
    
    Reference: https://www.psycopg.org/psycopg3/docs/basic/install.html
    "psycopg uses the libpq connection strings"
    
    By constructing from container kwargs, we avoid any SQLAlchemy dialect
    injection and guarantee libpq-safe format.
    """
    # Build DSN from container properties (not get_connection_url)
    # This is the psycopg-recommended way: keyword/value format
    host = postgres_container.get_container_host_ip()
    port = postgres_container.get_exposed_port(5432)
    user = postgres_container.username
    password = postgres_container.password
    dbname = postgres_container.dbname
    
    # Construct plain libpq DSN (postgresql://user:pass@host:port/dbname)
    dsn = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
    
    # VERIFY: DSN is libpq-safe (no SQLAlchemy dialect)
    # Per psycopg docs, only plain postgresql:// URLs are supported
    sqlalchemy_dialects = ["+psycopg", "+psycopg2", "+asyncpg", "+pg8000"]
    for dialect in sqlalchemy_dialects:
        if dialect in dsn:
            raise ValueError(
                f"postgres_dsn contains SQLAlchemy dialect '{dialect}': {dsn}\n"
                f"psycopg requires plain libpq URLs (postgresql://...).\n"
                f"This should not happen with direct construction."
            )
    
    # Ensure it's actually postgresql://
    if not dsn.startswith("postgresql://"):
        raise ValueError(
            f"postgres_dsn doesn't start with postgresql://: {dsn}\n"
            f"Expected format: postgresql://user:pass@host:port/dbname"
        )
    
    # Log for debugging
    import logging
    logging.getLogger(__name__).info(f"Built postgres DSN: postgresql://{user}:***@{host}:{port}/{dbname}")
    
    return dsn


@pytest.fixture(scope="function")
def postgres_env(postgres_dsn, monkeypatch):
    """
    Configure environment to use Postgres instead of SQLite.
    
    Sets DATABASE_URL and disables USE_SQLITE for this test.
    Initializes the Postgres schema before the test runs.
    """
    # Set environment variables BEFORE importing/using the postgres module
    monkeypatch.setenv("DATABASE_URL", postgres_dsn)
    monkeypatch.setenv("USE_SQLITE", "false")
    monkeypatch.delenv("BEADS_DB", raising=False)
    
    # CRITICAL: Reset cached settings and connection pool so they pick up new env vars
    # This must happen AFTER setting env vars but BEFORE calling init_postgres_schema
    import integration_coworker.config as config_module
    config_module._settings = None  # Reset cached settings
    
    from integration_coworker.persistence import postgres as pg_persistence
    pg_persistence.close_pool()  # Close and reset pool so it uses new DATABASE_URL
    
    # Initialize schema (all schemas including KG)
    pg_persistence.init_all_schemas()
    
    yield postgres_dsn
    
    # Cleanup: truncate all tables for next test
    try:
        import psycopg
        with psycopg.connect(postgres_dsn) as conn:
            with conn.cursor() as cur:
                # Get all tables in spec_silver schema
                cur.execute("""
                    SELECT table_name FROM information_schema.tables 
                    WHERE table_schema = 'spec_silver'
                """)
                tables = [row[0] for row in cur.fetchall()]
                
                # Truncate in dependency order (children first)
                truncate_order = [
                    'file_validation_rules', 'file_fields', 'record_layouts', 'file_specs',
                    'kg_workflow_steps', 'kg_edges', 'kg_nodes',
                    'spec_chunks', 'endpoints', 'schemas', 'entities',
                    'spec_sections', 'spec_documents', 'source_systems',
                    'integration_runs'
                ]
                for table in truncate_order:
                    if table in tables:
                        cur.execute(f"TRUNCATE TABLE spec_silver.{table} CASCADE")
                conn.commit()
    except Exception:
        pass  # Cleanup failure is not critical


@pytest.fixture(scope="function")
def postgres_connection(postgres_env):
    """
    Return a live psycopg connection to the test Postgres instance.
    
    Use this fixture when you need direct SQL access in tests.
    """
    import psycopg
    conn = psycopg.connect(postgres_env)
    yield conn
    conn.close()
