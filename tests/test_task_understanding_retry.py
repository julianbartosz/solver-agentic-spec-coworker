"""
Tests for Section 3.13: Task Understanding Robustness

Tests tenacity retry logic and degraded_mode flag.

V2.2: Updated to test TOON format instead of JSON.
V2.3: Updated to use call_llm_for_node (archetype-based).
"""
import pytest
from unittest.mock import patch, MagicMock

# Mark all tests in this module to skip database setup
pytestmark = pytest.mark.no_db

from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.nodes.understand_task import (
    understand_task,
    _call_llm_with_retry,
    _extract_action_verb,
    _extract_resource_noun,
)


def make_state(**kwargs) -> WorkflowState:
    """Create a test WorkflowState with defaults."""
    defaults = {
        "source_refs": [],
        "spec_refs": ["test.yaml"],
        "task_description": "Create a checkout session for payment",
        "provider_code": "test_provider",
    }
    defaults.update(kwargs)
    return WorkflowState(**defaults)


def make_toon_response(task_slug: str = "create_user", **extras) -> str:
    """Create a TOON-formatted response for testing."""
    lines = [f"task_slug={task_slug}"]
    lines.append("input_entities=[]")
    lines.append("output_entities=[]")
    lines.append("constraints.idempotency_required=false")
    lines.append("constraints.requires_webhooks=false")
    for key, value in extras.items():
        if isinstance(value, bool):
            lines.append(f"{key}={'true' if value else 'false'}")
        else:
            lines.append(f"{key}={value}")
    return "\n".join(lines)


class TestExtractActionVerb:
    """Tests for _extract_action_verb helper."""

    def test_extracts_create(self):
        assert _extract_action_verb("Create a new user") == "create"

    def test_extracts_update(self):
        assert _extract_action_verb("Update the customer record") == "update"

    def test_extracts_delete(self):
        assert _extract_action_verb("Delete an order") == "delete"

    def test_extracts_get(self):
        assert _extract_action_verb("Get user by ID") == "get"

    def test_extracts_fetch(self):
        assert _extract_action_verb("Fetch all products") == "fetch"

    def test_extracts_list(self):
        assert _extract_action_verb("List all invoices") == "list"

    def test_fallback_first_word(self):
        assert _extract_action_verb("Process the payment") == "process"

    def test_empty_returns_unknown(self):
        assert _extract_action_verb("") == "unknown"


class TestExtractResourceNoun:
    """Tests for _extract_resource_noun helper."""

    def test_extracts_user(self):
        assert _extract_resource_noun("Create a new user") == "user"

    def test_extracts_customer(self):
        assert _extract_resource_noun("Update the customer") == "customer"

    def test_extracts_order(self):
        assert _extract_resource_noun("Delete an order") == "order"

    def test_extracts_payment(self):
        assert _extract_resource_noun("Process the payment") == "payment"

    def test_fallback_second_word(self):
        assert _extract_resource_noun("Process request now") == "request"

    def test_single_word_returns_resource(self):
        assert _extract_resource_noun("Process") == "resource"


class TestCallLLMWithRetry:
    """Tests for _call_llm_with_retry function."""

    @patch("integration_coworker.graph.nodes.understand_task.call_llm_async_for_node")
    @pytest.mark.asyncio
    async def test_success_on_first_try(self, mock_llm):
        """Should return immediately on success."""
        mock_llm.return_value = make_toon_response("create_user")
        
        result = await _call_llm_with_retry("test prompt")
        
        assert result["task_slug"] == "create_user"
        assert mock_llm.call_count == 1

    @patch("integration_coworker.graph.nodes.understand_task.call_llm_async_for_node")
    @pytest.mark.asyncio
    async def test_retries_on_empty_response(self, mock_llm):
        """Should retry on empty response."""
        mock_llm.side_effect = ["", "", make_toon_response("create_user")]
        
        result = await _call_llm_with_retry("test prompt")
        
        assert result["task_slug"] == "create_user"
        assert mock_llm.call_count == 3

    @patch("integration_coworker.graph.nodes.understand_task.call_llm_async_for_node")
    @pytest.mark.asyncio
    async def test_retries_on_error_response(self, mock_llm):
        """Should retry on error response."""
        mock_llm.side_effect = [
            "error=rate limited",
            make_toon_response("create_user"),
        ]
        
        result = await _call_llm_with_retry("test prompt")
        
        assert result["task_slug"] == "create_user"
        assert mock_llm.call_count == 2

    @patch("integration_coworker.graph.nodes.understand_task.call_llm_async_for_node")
    @pytest.mark.asyncio
    async def test_retries_on_missing_task_slug(self, mock_llm):
        """Should retry on response missing task_slug."""
        mock_llm.side_effect = [
            "some_other_field=value",
            make_toon_response("create_user"),
        ]
        
        result = await _call_llm_with_retry("test prompt")
        
        assert result["task_slug"] == "create_user"
        assert mock_llm.call_count == 2


class TestUnderstandTaskDegradedMode:
    """Tests for degraded_mode handling in understand_task."""

    @patch("integration_coworker.graph.nodes.understand_task.call_llm_async_for_node")
    @pytest.mark.asyncio
    async def test_llm_success_no_degraded_mode(self, mock_llm):
        """Successful LLM call should not set degraded_mode."""
        mock_llm.return_value = """task_slug=create_checkout
input_entities=[]
output_entities=[CheckoutSession]
constraints.idempotency_required=false
constraints.requires_webhooks=false"""
        
        state = make_state()
        result = await understand_task(state)
        
        assert result.degraded_mode is False
        assert result.degraded_reason is None
        assert result.integration_task is not None
        assert result.integration_task.task_slug == "create_checkout"

    @patch("integration_coworker.graph.nodes.understand_task.call_llm_async_for_node")
    @pytest.mark.asyncio
    async def test_llm_failure_sets_degraded_mode(self, mock_llm):
        """Failed LLM calls should set degraded_mode."""
        mock_llm.side_effect = Exception("LLM unavailable")
        
        state = make_state()
        result = await understand_task(state)
        
        assert result.degraded_mode is True
        assert result.degraded_reason is not None
        assert "Task understanding failed" in result.degraded_reason
        # Should still have integration_task from heuristics
        assert result.integration_task is not None

    @patch("integration_coworker.graph.nodes.understand_task.call_llm_async_for_node")
    @pytest.mark.asyncio
    async def test_all_retries_exhausted_sets_degraded_mode(self, mock_llm):
        """Exhausted retries should set degraded_mode."""
        mock_llm.return_value = ""  # Always returns invalid (empty)
        
        state = make_state()
        result = await understand_task(state)
        
        assert result.degraded_mode is True
        assert result.integration_task is not None

    @patch("integration_coworker.graph.nodes.understand_task.call_llm_async_for_node")
    @pytest.mark.asyncio
    async def test_heuristic_fallback_produces_valid_task(self, mock_llm):
        """Heuristic fallback should produce valid IntegrationTask."""
        mock_llm.side_effect = Exception("LLM unavailable")
        
        state = make_state(
            task_description="Create a checkout session for payment",
            provider_code="stripe",
        )
        result = await understand_task(state)
        
        assert result.integration_task is not None
        assert result.integration_task.provider_code == "stripe"
        assert result.integration_task.description == "Create a checkout session for payment"
        # Heuristic should extract "create" and "checkout"
        assert "create" in result.integration_task.task_slug.lower()

    @patch("integration_coworker.graph.nodes.understand_task.call_llm_async_for_node")
    @pytest.mark.asyncio
    async def test_tracks_task_source(self, mock_llm):
        """Should track whether task came from LLM or heuristic."""
        mock_llm.return_value = make_toon_response("create_checkout")
        
        state = make_state()
        result = await understand_task(state)
        
        # LLM success should set source to "llm"
        assert hasattr(result.integration_task, "_task_source")
        assert result.integration_task._task_source == "llm"

    @patch("integration_coworker.graph.nodes.understand_task.call_llm_async_for_node")
    @pytest.mark.asyncio
    async def test_heuristic_tracks_task_source(self, mock_llm):
        """Heuristic fallback should track source as 'heuristic'."""
        mock_llm.side_effect = Exception("LLM unavailable")
        
        state = make_state()
        result = await understand_task(state)
        
        assert hasattr(result.integration_task, "_task_source")
        assert result.integration_task._task_source == "heuristic"


class TestUnderstandTaskCompletedSteps:
    """Tests for completed_steps tracking."""

    @patch("integration_coworker.graph.nodes.understand_task.call_llm_async_for_node")
    @pytest.mark.asyncio
    async def test_adds_to_completed_steps(self, mock_llm):
        """Should add understand_task to completed_steps."""
        mock_llm.return_value = make_toon_response("test")
        
        state = make_state()
        result = await understand_task(state)
        
        assert "understand_task" in result.completed_steps

    @pytest.mark.asyncio
    async def test_no_task_description_adds_error(self):
        """Missing task_description should add error."""
        state = make_state(task_description="")
        result = await understand_task(state)
        
        assert "No task_description provided" in result.errors
        assert "understand_task" in result.completed_steps
