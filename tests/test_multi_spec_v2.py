"""
Tests for Section 3.12: Multi-Spec Support

Tests pending_specs population and multi-spec Silver model building.
"""
import pytest

from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.nodes.plan_run import plan_run, infer_provider_code
from integration_coworker.graph.nodes.build_silver_api_model import build_silver_api_model


def make_state(**kwargs) -> WorkflowState:
    """Create a test WorkflowState with defaults."""
    defaults = {
        "source_refs": [],
        "spec_refs": [],
        "task_description": "test task",
    }
    defaults.update(kwargs)
    return WorkflowState(**defaults)


class TestPendingSpecsPopulation:
    """Tests for pending_specs population in plan_run."""

    def test_single_spec_populates_pending_specs(self):
        """Single spec should create one pending_specs entry."""
        state = make_state(spec_refs=["tests/fixtures/mock_payments_openapi.yaml"])
        
        result = plan_run(state)
        
        assert len(result.pending_specs) == 1
        assert result.pending_specs[0]["index"] == 0
        assert result.pending_specs[0]["ref"] == "tests/fixtures/mock_payments_openapi.yaml"
        assert result.pending_specs[0]["is_primary"] is True
        assert "provider_code" in result.pending_specs[0]

    def test_multiple_specs_populates_pending_specs(self):
        """Multiple specs should create entries for each."""
        state = make_state(spec_refs=[
            "https://api.stripe.com/openapi.yaml",
            "https://api.github.com/openapi.yaml",
            "/local/path/custom_spec.yaml",
        ])
        
        result = plan_run(state)
        
        assert len(result.pending_specs) == 3
        
        # First is primary
        assert result.pending_specs[0]["is_primary"] is True
        assert result.pending_specs[0]["index"] == 0
        
        # Others are supporting
        assert result.pending_specs[1]["is_primary"] is False
        assert result.pending_specs[1]["index"] == 1
        assert result.pending_specs[2]["is_primary"] is False
        assert result.pending_specs[2]["index"] == 2

    def test_pending_specs_have_inferred_provider_codes(self):
        """Each pending spec should have inferred provider_code."""
        state = make_state(spec_refs=[
            "https://api.stripe.com/openapi.yaml",
            "https://api.github.com/openapi.yaml",
        ])
        
        result = plan_run(state)
        
        # Provider codes should be inferred from URLs
        assert result.pending_specs[0]["provider_code"] == "stripe"
        assert result.pending_specs[1]["provider_code"] == "github"

    def test_primary_provider_code_set_from_first_spec(self):
        """State.provider_code should be set from first spec."""
        state = make_state(spec_refs=[
            "https://api.hubspot.com/openapi.yaml",
            "https://api.stripe.com/openapi.yaml",
        ])
        
        result = plan_run(state)
        
        assert result.provider_code == "hubspot"

    def test_empty_spec_refs_raises(self):
        """Empty spec_refs should raise ValueError."""
        state = make_state(spec_refs=[])
        
        with pytest.raises(ValueError, match="At least one spec_ref"):
            plan_run(state)


class TestMultiSpecSilverModel:
    """Tests for multi-spec Silver model building."""

    def test_builds_from_multiple_specs(self):
        """Should extract endpoints from multiple specs."""
        spec1 = {
            "_source_uri": "spec1.yaml",
            "paths": {
                "/users": {
                    "get": {
                        "operationId": "listUsers",
                        "summary": "List users",
                    }
                }
            },
            "components": {"schemas": {}},
        }
        spec2 = {
            "_source_uri": "spec2.yaml",
            "paths": {
                "/orders": {
                    "post": {
                        "operationId": "createOrder",
                        "summary": "Create order",
                    }
                }
            },
            "components": {"schemas": {}},
        }
        
        state = make_state(
            spec_refs=["spec1.yaml", "spec2.yaml"],
            task_description="test",
        )
        state.plan = {"openapi_specs": [spec1, spec2]}
        state.pending_specs = [
            {"index": 0, "ref": "spec1.yaml", "provider_code": "users_api", "is_primary": True},
            {"index": 1, "ref": "spec2.yaml", "provider_code": "orders_api", "is_primary": False},
        ]
        
        result = build_silver_api_model(state)
        
        # Should have endpoints from both specs
        assert len(result.endpoints) == 2
        paths = [ep.path for ep in result.endpoints]
        assert "/users" in paths
        assert "/orders" in paths

    def test_endpoints_tagged_with_source_uri(self):
        """Endpoints should be tagged with their source URI."""
        spec = {
            "_source_uri": "https://api.example.com/spec.yaml",
            "paths": {
                "/test": {"get": {"operationId": "test"}}
            },
            "components": {"schemas": {}},
        }
        
        state = make_state(spec_refs=["https://api.example.com/spec.yaml"])
        state.plan = {"openapi_specs": [spec]}
        
        result = build_silver_api_model(state)
        
        assert len(result.endpoints) == 1
        assert result.endpoints[0]._source_uri == "https://api.example.com/spec.yaml"

    def test_uses_parsed_specs_over_legacy(self):
        """Should prefer parsed_specs over plan['openapi_specs']."""
        legacy_spec = {
            "_source_uri": "legacy.yaml",
            "paths": {"/legacy": {"get": {"operationId": "legacy"}}},
            "components": {"schemas": {}},
        }
        new_spec = {
            "_source_uri": "new.yaml",
            "paths": {"/new": {"get": {"operationId": "new"}}},
            "components": {"schemas": {}},
        }
        
        state = make_state(spec_refs=["new.yaml"])
        state.plan = {"openapi_specs": [legacy_spec]}  # Legacy path
        state.parsed_specs = [new_spec]  # V2 path
        
        result = build_silver_api_model(state)
        
        # Should use parsed_specs (V2 path)
        assert len(result.endpoints) == 1
        assert result.endpoints[0].operation_id == "new"

    def test_enriches_with_pending_specs_provider_code(self):
        """Endpoints should be tagged with provider_code from pending_specs."""
        spec = {
            "_source_uri": "spec.yaml",
            "paths": {
                "/test": {"get": {"operationId": "test"}}
            },
            "components": {"schemas": {}},
        }
        
        state = make_state(spec_refs=["spec.yaml"])
        state.plan = {"openapi_specs": [spec]}
        state.pending_specs = [
            {"index": 0, "ref": "spec.yaml", "provider_code": "custom_provider", "is_primary": True}
        ]
        
        result = build_silver_api_model(state)
        
        assert len(result.endpoints) == 1
        assert hasattr(result.endpoints[0], "_provider_code")
        assert result.endpoints[0]._provider_code == "custom_provider"


class TestWorkflowStateDegradedMode:
    """Tests for degraded_mode and degraded_reason fields."""

    def test_degraded_mode_default_false(self):
        """degraded_mode should default to False."""
        state = make_state()
        assert state.degraded_mode is False
        assert state.degraded_reason is None

    def test_degraded_mode_can_be_set(self):
        """degraded_mode should be settable."""
        state = make_state()
        state.degraded_mode = True
        state.degraded_reason = "LLM failed after retries"
        
        assert state.degraded_mode is True
        assert state.degraded_reason == "LLM failed after retries"


class TestWorkflowStateParsedSpecs:
    """Tests for parsed_specs field."""

    def test_parsed_specs_default_empty(self):
        """parsed_specs should default to empty list."""
        state = make_state()
        assert state.parsed_specs == []

    def test_parsed_specs_can_store_multiple_specs(self):
        """parsed_specs should store multiple specs."""
        state = make_state()
        state.parsed_specs = [
            {"_source_uri": "spec1.yaml", "paths": {}},
            {"_source_uri": "spec2.yaml", "paths": {}},
        ]
        
        assert len(state.parsed_specs) == 2
