import os

import pytest

from integration_coworker.config import reset_settings
from integration_coworker.graph import runtime


# Force SQLite for these tests so the autouse DB fixture initializes the right schema
os.environ["USE_SQLITE"] = "true"
reset_settings()


@pytest.mark.asyncio
@pytest.mark.requires_aiosqlite
async def test_async_checkpointer_context_yields_saver(monkeypatch):
    monkeypatch.setenv("USE_SQLITE", "true")
    reset_settings()
    runtime.reset_checkpointer()

    async with runtime.async_checkpointer_context() as saver:
        assert hasattr(saver, "get_next_version")
        # Async saver should expose async tuple retrieval
        assert hasattr(saver, "aget_tuple")

    runtime.reset_checkpointer()


@pytest.mark.requires_langgraph_checkpoint
@pytest.mark.requires_aiosqlite
def test_checkpointer_context_yields_saver(monkeypatch):
    monkeypatch.setenv("USE_SQLITE", "true")
    reset_settings()
    runtime.reset_checkpointer()

    with runtime.checkpointer_context() as saver:
        assert hasattr(saver, "get_next_version")

    runtime.reset_checkpointer()
