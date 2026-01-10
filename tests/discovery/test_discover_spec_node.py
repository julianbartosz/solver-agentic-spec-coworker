"""
Tests for the discover_spec graph node.

Tests the integration between the discovery module and the LangGraph workflow.
"""

import pytest
from unittest.mock import patch, MagicMock
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from integration_coworker.graph.nodes.discover_spec import (
    discover_spec,
    discover_spec_async,
    _is_discovery_enabled,
)
from integration_coworker.graph.state import WorkflowState
from integration_coworker.discovery import DiscoveryResult


@dataclass
class MockOptions:
    """Mock options for testing."""
    discovery_enabled: Optional[bool] = None


def make_state(
    spec_refs: Optional[List[str]] = None,
    task_description: str = "",
    options: Optional[MockOptions] = None,
) -> WorkflowState:
    """Create a WorkflowState for testing."""
    state = WorkflowState(
        source_refs=[],  # Required field
        spec_refs=spec_refs or [],
        task_description=task_description,
    )
    state.run_id = "test-run-id"
    state.options = options
    state.completed_steps = []
    state.errors = []
    return state


class TestIsDiscoveryEnabled:
    """Tests for _is_discovery_enabled helper."""

    def test_options_override_true(self):
        """Options can explicitly enable discovery."""
        state = make_state(options=MockOptions(discovery_enabled=True))
        with patch("integration_coworker.graph.nodes.discover_spec.get_settings") as mock:
            mock.return_value.discovery_enabled = False
            assert _is_discovery_enabled(state) is True

    def test_options_override_false(self):
        """Options can explicitly disable discovery."""
        state = make_state(options=MockOptions(discovery_enabled=False))
        with patch("integration_coworker.graph.nodes.discover_spec.get_settings") as mock:
            mock.return_value.discovery_enabled = True
            assert _is_discovery_enabled(state) is False

    def test_falls_back_to_settings(self):
        """Falls back to global setting if options not set."""
        state = make_state(options=None)
        with patch("integration_coworker.graph.nodes.discover_spec.get_settings") as mock:
            mock.return_value.discovery_enabled = True
            assert _is_discovery_enabled(state) is True

    def test_falls_back_when_option_is_none(self):
        """Falls back to global setting if option is None."""
        state = make_state(options=MockOptions(discovery_enabled=None))
        with patch("integration_coworker.graph.nodes.discover_spec.get_settings") as mock:
            mock.return_value.discovery_enabled = True
            assert _is_discovery_enabled(state) is True


class TestDiscoverSpecSkipConditions:
    """Tests for conditions where discover_spec should skip."""

    def test_skips_when_spec_refs_provided(self):
        """Skip discovery when spec_refs already provided."""
        state = make_state(
            spec_refs=["https://example.com/spec.yaml"],
            task_description="Do something with the API",
        )
        
        result = discover_spec(state)
        
        assert result.discovery_source == "user_provided"
        assert result.discovery_confidence == 1.0
        assert "discover_spec" in result.completed_steps
        assert result.spec_refs == ["https://example.com/spec.yaml"]

    def test_error_when_discovery_disabled(self):
        """Raise error when spec_refs empty and discovery disabled."""
        state = make_state(
            spec_refs=[],
            task_description="Process payment with Stripe",
        )
        
        with patch("integration_coworker.graph.nodes.discover_spec.get_settings") as mock:
            mock.return_value.discovery_enabled = False
            
            with pytest.raises(ValueError, match="discovery is disabled"):
                discover_spec(state)

    def test_error_when_task_empty(self):
        """Raise error when task_description is empty."""
        state = make_state(
            spec_refs=[],
            task_description="",
        )
        
        with patch("integration_coworker.graph.nodes.discover_spec.get_settings") as mock:
            mock.return_value.discovery_enabled = True
            
            with pytest.raises(ValueError, match="task_description is empty"):
                discover_spec(state)

    def test_error_when_task_whitespace(self):
        """Raise error when task_description is only whitespace."""
        state = make_state(
            spec_refs=[],
            task_description="   \n\t  ",
        )
        
        with patch("integration_coworker.graph.nodes.discover_spec.get_settings") as mock:
            mock.return_value.discovery_enabled = True
            
            with pytest.raises(ValueError, match="task_description is empty"):
                discover_spec(state)


class TestDiscoverSpecSuccess:
    """Tests for successful discovery scenarios."""

    def test_successful_discovery(self):
        """Successful discovery updates state correctly."""
        state = make_state(
            spec_refs=[],
            task_description="Process payment with Stripe",
        )
        
        mock_result = MagicMock(spec=DiscoveryResult)
        mock_result.success = True
        mock_result.spec_url = "https://api.apis.guru/v2/specs/stripe.com/2023-10-16/openapi.yaml"
        mock_result.source = "apis_guru"
        mock_result.confidence = 0.85
        mock_result.api_name = "stripe.com"
        mock_result.provider = "Stripe"
        mock_result.to_dict.return_value = {
            "spec_url": mock_result.spec_url,
            "source": mock_result.source,
            "confidence": mock_result.confidence,
        }
        
        with patch("integration_coworker.graph.nodes.discover_spec.get_settings") as mock_settings:
            mock_settings.return_value.discovery_enabled = True
            mock_settings.return_value.discovery_max_candidates = 3
            
            with patch(
                "integration_coworker.graph.nodes.discover_spec.resolve_spec_from_task"
            ) as mock_resolve:
                # Make it async-compatible
                import asyncio
                mock_resolve.return_value = mock_result
                
                result = discover_spec(state)
        
        assert result.spec_refs == [mock_result.spec_url]
        assert result.discovery_source == "apis_guru"
        assert result.discovery_confidence == 0.85
        assert result.discovered_specs is not None
        assert "discover_spec" in result.completed_steps

    def test_discovery_failure_raises(self):
        """Failed discovery raises ValueError with error message."""
        state = make_state(
            spec_refs=[],
            task_description="Do something with an API",
        )
        
        mock_result = MagicMock(spec=DiscoveryResult)
        mock_result.success = False
        mock_result.error = "No matching APIs found"
        
        with patch("integration_coworker.graph.nodes.discover_spec.get_settings") as mock_settings:
            mock_settings.return_value.discovery_enabled = True
            mock_settings.return_value.discovery_max_candidates = 3
            
            with patch(
                "integration_coworker.graph.nodes.discover_spec.resolve_spec_from_task"
            ) as mock_resolve:
                mock_resolve.return_value = mock_result
                
                with pytest.raises(ValueError, match="Could not discover spec"):
                    discover_spec(state)


class TestDiscoverSpecAsync:
    """Tests for the async discover_spec_async function."""

    @pytest.mark.asyncio
    async def test_async_skip_when_spec_provided(self):
        """Async version skips when spec_refs provided."""
        state = make_state(
            spec_refs=["https://example.com/spec.yaml"],
            task_description="Do something",
        )
        
        result = await discover_spec_async(state)
        
        assert result.discovery_source == "user_provided"
        assert result.discovery_confidence == 1.0

    @pytest.mark.asyncio
    async def test_async_error_when_disabled(self):
        """Async version raises when discovery disabled."""
        state = make_state(
            spec_refs=[],
            task_description="Process payment with Stripe",
        )
        
        with patch("integration_coworker.graph.nodes.discover_spec.get_settings") as mock:
            mock.return_value.discovery_enabled = False
            
            with pytest.raises(ValueError, match="discovery is disabled"):
                await discover_spec_async(state)


class TestEdgeCases:
    """Edge case tests."""

    def test_multiple_spec_refs_no_discovery(self):
        """Multiple existing spec_refs don't trigger discovery."""
        state = make_state(
            spec_refs=[
                "https://example.com/spec1.yaml",
                "https://example.com/spec2.yaml",
            ],
            task_description="Use both APIs",
        )
        
        result = discover_spec(state)
        
        assert len(result.spec_refs) == 2
        assert result.discovery_source == "user_provided"

    def test_resolver_exception_wrapped(self):
        """Exception from resolver is wrapped in ValueError."""
        state = make_state(
            spec_refs=[],
            task_description="Process payment with Stripe",
        )
        
        with patch("integration_coworker.graph.nodes.discover_spec.get_settings") as mock_settings:
            mock_settings.return_value.discovery_enabled = True
            mock_settings.return_value.discovery_max_candidates = 3
            
            with patch(
                "integration_coworker.graph.nodes.discover_spec.resolve_spec_from_task"
            ) as mock_resolve:
                mock_resolve.side_effect = Exception("Network error")
                
                with pytest.raises(ValueError, match="Discovery failed"):
                    discover_spec(state)
