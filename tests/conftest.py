"""
Pytest configuration and shared fixtures for integration_coworker tests.
"""
import hashlib
import pytest
import sqlite3
from typing import List, Optional
from unittest.mock import MagicMock

from integration_coworker.persistence import db


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
    
    # Patch call_llm and call_llm_json
    def mock_call_llm(*args, **kwargs):
        return controller.get_response()
    
    try:
        monkeypatch.setattr(
            "integration_coworker.llm.client.call_llm",
            mock_call_llm
        )
        monkeypatch.setattr(
            "integration_coworker.llm.client.call_llm_json",
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

