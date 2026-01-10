import asyncio
from unittest.mock import patch

import pytest

from integration_coworker.graph.nodes.generate_code_and_tests import _apply_self_review_if_enabled
from integration_coworker.config.profiles import PROFILES


@pytest.mark.asyncio
@pytest.mark.no_db
async def test_self_review_skips_in_mock_mode(monkeypatch):
    # Force mock mode
    monkeypatch.setenv("USE_MOCK_LLM", "true")
    monkeypatch.delenv("LLM_MODE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    # Guard to ensure the LLM client path is not invoked
    with patch("integration_coworker.llm.client.get_llm_client_for_node") as get_client:
        code, success = await _apply_self_review_if_enabled(
            code="print('hi')\n",
            artifact_type="client",
            module_name="foo",
            state=type("State", (), {"task_description": "desc", "options": None})(),
            profile=PROFILES["production"],
        )
        assert success is True
        assert code.startswith("print")
        get_client.assert_not_called()
