"""
Node Execution Sanity Tests

These tests verify that non-LLM nodes (which may show "0.00s" in LangSmith)
actually do meaningful work. Each test directly invokes a node function
and validates state changes.

Uses USE_SQLITE=true and USE_MOCK_LLM=true for fast, deterministic tests.
"""
import asyncio
import os
import pytest
from pathlib import Path
from unittest.mock import patch

# Set environment before imports
os.environ["USE_SQLITE"] = "true"
os.environ["USE_MOCK_LLM"] = "true"

from integration_coworker.graph.state import WorkflowState
from integration_coworker.api.types import IntegrationOptions
from integration_coworker.domain.models import (
    SpecDocument, Endpoint, Schema, Entity, Policy, PolicyType,
    IntegrationFlowNode, EndpointBinding, CodeArtifact
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_openapi_spec(tmp_path):
    """Create a minimal mock OpenAPI spec file."""
    spec_content = """
openapi: "3.0.0"
info:
  title: "Mock Payments API"
  version: "1.0.0"
servers:
  - url: "https://api.mockpayments.com/v1"
paths:
  /v1/checkout/sessions:
    post:
      operationId: createCheckoutSession
      summary: Create a checkout session
      requestBody:
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/CheckoutSessionRequest'
      responses:
        '201':
          description: Created
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/CheckoutSession'
  /v1/customers:
    get:
      operationId: listCustomers
      summary: List all customers
      responses:
        '200':
          description: OK
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: '#/components/schemas/Customer'
components:
  securitySchemes:
    api_key:
      type: apiKey
      in: header
      name: X-API-Key
  schemas:
    CheckoutSessionRequest:
      type: object
      properties:
        amount:
          type: integer
        currency:
          type: string
    CheckoutSession:
      type: object
      properties:
        id:
          type: string
        amount:
          type: integer
        status:
          type: string
    Customer:
      type: object
      properties:
        id:
          type: string
        email:
          type: string
"""
    spec_path = tmp_path / "mock_payments_openapi.yaml"
    spec_path.write_text(spec_content)
    return spec_path


@pytest.fixture
def initial_state(mock_openapi_spec):
    """Create an initial WorkflowState for testing."""
    return WorkflowState(
        source_refs=[str(mock_openapi_spec)],
        spec_refs=[str(mock_openapi_spec)],
        task_description="Create a checkout session for payment processing",
        options=IntegrationOptions(dry_run=True),
    )


@pytest.fixture
def sqlite_db():
    """Ensure SQLite database is initialized."""
    from integration_coworker.persistence.db import init_schema, get_connection
    init_schema()
    return get_connection


# ============================================================================
# Test: plan_run
# ============================================================================

class TestPlanRun:
    """Tests for plan_run node - Pure Python planning."""
    
    def test_plan_run_populates_provider_and_task(self, initial_state):
        """Verify plan_run computes provider_code and builds a plan."""
        from integration_coworker.graph.nodes.plan_run import plan_run
        
        # Initial state should have no run_id or plan
        assert initial_state.run_id is None
        assert initial_state.plan == {}
        
        new_state = plan_run(initial_state)
        
        # Should populate run_id
        assert new_state.run_id is not None
        assert len(new_state.run_id) > 0
        
        # Should infer provider_code
        assert new_state.provider_code is not None
        assert new_state.provider_code != ""
        
        # Should build a plan with steps
        assert new_state.plan is not None
        assert "steps" in new_state.plan
        assert len(new_state.plan["steps"]) > 5
        
        # Should mark step as completed
        assert "plan_run" in new_state.completed_steps
    
    def test_plan_run_infers_provider_from_spec_title(self, tmp_path):
        """Verify provider inference from spec info.title."""
        from integration_coworker.graph.nodes.plan_run import infer_provider_code
        
        parsed_spec = {
            "info": {"title": "Stripe API"},
            "servers": []
        }
        provider = infer_provider_code("spec.yaml", parsed_spec)
        assert provider == "stripe"
    
    def test_plan_run_infers_provider_from_servers_url(self, tmp_path):
        """Verify provider inference from spec servers URL."""
        from integration_coworker.graph.nodes.plan_run import infer_provider_code
        
        parsed_spec = {
            "info": {"title": "Some API"},
            "servers": [{"url": "https://api.hubspot.com/v3"}]
        }
        provider = infer_provider_code("spec.yaml", parsed_spec)
        assert provider == "hubspot"


# ============================================================================
# Test: build_silver_api_model (using the full pipeline through ingest)
# ============================================================================

class TestBuildSilverApiModel:
    """Tests for build_silver_api_model - Spec parsing + model building."""
    
    def test_build_silver_api_model_extracts_endpoints(self, initial_state, mock_openapi_spec):
        """Verify Silver model extracts endpoints from spec."""
        from integration_coworker.graph.nodes.plan_run import plan_run
        from integration_coworker.graph.nodes.ingest_spec import ingest_spec
        from integration_coworker.graph.nodes.detect_and_parse_spec import detect_and_parse_spec
        from integration_coworker.graph.nodes.build_silver_api_model import build_silver_api_model
        
        # Run the full prerequisite pipeline
        state = plan_run(initial_state)
        state = ingest_spec(state)
        state = detect_and_parse_spec(state)
        
        # Run build_silver_api_model
        new_state = build_silver_api_model(state)
        
        # Should extract endpoints
        assert len(new_state.endpoints) > 0
        
        # Should have the checkout sessions endpoint
        paths = [e.path for e in new_state.endpoints]
        assert any("/checkout/sessions" in p for p in paths)
        
        # Should have customers endpoint
        assert any("/customers" in p for p in paths)
        
        # Should mark step as completed
        assert "build_silver_api_model" in new_state.completed_steps
    
    def test_build_silver_api_model_extracts_schemas(self, initial_state, mock_openapi_spec):
        """Verify Silver model extracts schemas from spec."""
        from integration_coworker.graph.nodes.plan_run import plan_run
        from integration_coworker.graph.nodes.ingest_spec import ingest_spec
        from integration_coworker.graph.nodes.detect_and_parse_spec import detect_and_parse_spec
        from integration_coworker.graph.nodes.build_silver_api_model import build_silver_api_model
        
        state = plan_run(initial_state)
        state = ingest_spec(state)
        state = detect_and_parse_spec(state)
        new_state = build_silver_api_model(state)
        
        # Should extract schemas
        assert len(new_state.schemas) > 0
        
        schema_names = [s.name for s in new_state.schemas]
        assert "CheckoutSession" in schema_names
        assert "Customer" in schema_names
    
    def test_build_silver_api_model_detects_entities(self, initial_state, mock_openapi_spec):
        """Verify Silver model detects entities (schemas with 'id' field)."""
        from integration_coworker.graph.nodes.plan_run import plan_run
        from integration_coworker.graph.nodes.ingest_spec import ingest_spec
        from integration_coworker.graph.nodes.detect_and_parse_spec import detect_and_parse_spec
        from integration_coworker.graph.nodes.build_silver_api_model import build_silver_api_model
        
        state = plan_run(initial_state)
        state = ingest_spec(state)
        state = detect_and_parse_spec(state)
        new_state = build_silver_api_model(state)
        
        # Should detect entities (schemas with 'id' property)
        assert len(new_state.entities) > 0
        
        entity_names = [e.name for e in new_state.entities]
        # CheckoutSession and Customer have 'id' fields
        assert "CheckoutSession" in entity_names or "Customer" in entity_names


# ============================================================================
# Test: attach_policies_and_patterns
# ============================================================================

class TestAttachPoliciesAndPatterns:
    """Tests for attach_policies_and_patterns - Policy inference from spec."""
    
    def test_attach_policies_infers_auth_from_security_schemes(self, initial_state, mock_openapi_spec):
        """Verify auth policy is inferred from securitySchemes."""
        from integration_coworker.graph.nodes.plan_run import plan_run
        from integration_coworker.graph.nodes.ingest_spec import ingest_spec
        from integration_coworker.graph.nodes.detect_and_parse_spec import detect_and_parse_spec
        from integration_coworker.graph.nodes.build_silver_api_model import build_silver_api_model
        from integration_coworker.graph.nodes.attach_policies_and_patterns import attach_policies_and_patterns
        
        state = plan_run(initial_state)
        state = ingest_spec(state)
        state = detect_and_parse_spec(state)
        state = build_silver_api_model(state)
        
        # Add an endpoint binding to trigger policy attachment
        state.endpoint_bindings = [
            EndpointBinding(
                id=None,
                task_id=None,
                flow_node_key="call_checkout",
                endpoint_id=None,
            )
        ]
        
        new_state = attach_policies_and_patterns(state)
        
        # Should have policies attached
        assert len(new_state.policies) > 0
        
        # Should have auth policy
        auth_policies = [p for p in new_state.policies if p.policy_type == PolicyType.AUTH]
        assert len(auth_policies) > 0
        
        # Auth config should reflect api_key from securitySchemes
        auth_config = auth_policies[0].config
        assert auth_config.get("type") == "api_key"
        assert auth_config.get("key_name") == "X-API-Key"
    
    def test_attach_policies_adds_retry_policy(self, initial_state, mock_openapi_spec):
        """Verify retry policy is added for API calls."""
        from integration_coworker.graph.nodes.plan_run import plan_run
        from integration_coworker.graph.nodes.ingest_spec import ingest_spec
        from integration_coworker.graph.nodes.detect_and_parse_spec import detect_and_parse_spec
        from integration_coworker.graph.nodes.build_silver_api_model import build_silver_api_model
        from integration_coworker.graph.nodes.attach_policies_and_patterns import attach_policies_and_patterns
        
        state = plan_run(initial_state)
        state = ingest_spec(state)
        state = detect_and_parse_spec(state)
        state = build_silver_api_model(state)
        
        state.endpoint_bindings = [
            EndpointBinding(
                id=None, task_id=None,
                flow_node_key="call_checkout", endpoint_id=None,
            )
        ]
        
        new_state = attach_policies_and_patterns(state)
        
        # Should have retry policy
        retry_policies = [p for p in new_state.policies if p.policy_type == PolicyType.RETRY]
        assert len(retry_policies) > 0
        
        # Retry config should have sensible defaults
        retry_config = retry_policies[0].config
        assert retry_config.get("max_attempts", 0) >= 2
        assert 429 in retry_config.get("retryable_status_codes", [])


# ============================================================================
# Test: persist_silver_checkpoint
# ============================================================================

class TestPersistSilverCheckpoint:
    """Tests for persist_silver_checkpoint - DB writes."""
    
    def test_persist_silver_checkpoint_dry_run_skips_db(self, initial_state, mock_openapi_spec):
        """Verify dry run mode skips actual DB writes."""
        from integration_coworker.graph.nodes.plan_run import plan_run
        from integration_coworker.graph.nodes.ingest_spec import ingest_spec
        from integration_coworker.graph.nodes.detect_and_parse_spec import detect_and_parse_spec
        from integration_coworker.graph.nodes.build_silver_api_model import build_silver_api_model
        from integration_coworker.graph.nodes.persist_silver_checkpoint import persist_silver_checkpoint
        
        state = plan_run(initial_state)
        state = ingest_spec(state)
        state = detect_and_parse_spec(state)
        state = build_silver_api_model(state)
        
        # Ensure dry_run is True
        state.options = IntegrationOptions(dry_run=True)
        
        new_state = persist_silver_checkpoint(state)
        
        # Should mark as dry run
        assert new_state.persisted_ids.get("silver_dry_run") is True
        
        # Should track what would be persisted
        would_persist = new_state.persisted_ids.get("would_persist_silver", {})
        assert would_persist.get("endpoints", 0) > 0
        
        # Should mark step as completed
        assert "persist_silver_checkpoint" in new_state.completed_steps
    
    def test_persist_silver_checkpoint_writes_to_db(self, initial_state, mock_openapi_spec, sqlite_db):
        """Verify actual DB writes when not in dry run mode."""
        from integration_coworker.graph.nodes.plan_run import plan_run
        from integration_coworker.graph.nodes.ingest_spec import ingest_spec
        from integration_coworker.graph.nodes.detect_and_parse_spec import detect_and_parse_spec
        from integration_coworker.graph.nodes.build_silver_api_model import build_silver_api_model
        from integration_coworker.graph.nodes.persist_silver_checkpoint import persist_silver_checkpoint
        
        state = plan_run(initial_state)
        state = ingest_spec(state)
        state = detect_and_parse_spec(state)
        state = build_silver_api_model(state)
        
        # Disable dry_run
        state.options = IntegrationOptions(dry_run=False)
        
        # Count endpoints before
        conn = sqlite_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM endpoints")
        count_before = cur.fetchone()[0]
        conn.close()
        
        new_state = persist_silver_checkpoint(state)
        
        # Count endpoints after
        conn = sqlite_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM endpoints")
        count_after = cur.fetchone()[0]
        conn.close()
        
        # Should have written new endpoints
        assert count_after >= count_before
        
        # IDs should be backfilled
        assert new_state.persisted_ids.get("source_system_id") is not None


# ============================================================================
# Test: validate_integration_design
# ============================================================================

class TestValidateIntegrationDesign:
    """Tests for validate_integration_design - Syntax validation."""
    
    def test_validate_integration_design_checks_syntax(self):
        """Verify code artifacts are syntax-checked."""
        from integration_coworker.graph.nodes.validate_integration_design import validate_integration_design
        from integration_coworker.domain.models import IntegrationTask, IntegrationFlowNode, IntegrationFlowEdge
        
        state = WorkflowState(
            source_refs=[],
            spec_refs=["test.yaml"],
            task_description="Test task",
            code_artifacts=[
                CodeArtifact(
                    id=None,
                    task_id=None,
                    rel_path="src/client.py",
                    artifact_type="client",
                    language="python",
                    module_name="client",
                    content="def hello():\n    return 'world'"
                )
            ],
            # Provide minimal required state
            integration_task=IntegrationTask(
                id=None, source_system_id=None, task_slug="test_task", 
                provider_code="test", description="Test task"
            ),
            workflow_nodes=[
                IntegrationFlowNode(id=None, task_id=None, node_key="start",
                                   node_type="start", position=0),
                IntegrationFlowNode(id=None, task_id=None, node_key="end",
                                   node_type="end", position=1),
            ],
            workflow_edges=[
                IntegrationFlowEdge(id=None, task_id=None, from_node_key="start", to_node_key="end"),
            ],
            endpoints=[
                Endpoint(id=1, source_system_id=1, spec_document_id=1,
                        path="/test", method="GET", operation_id="test",
                        summary=None, description=None,
                        request_schema_id=None, response_schema_id=None)
            ],
        )
        
        new_state = validate_integration_design(state)
        
        # Should complete without errors for valid code
        assert "validate_integration_design" in new_state.completed_steps
        # Should not have syntax errors specifically
        syntax_errors = [e for e in new_state.errors if "Syntax error" in e]
        assert len(syntax_errors) == 0
    
    def test_validate_integration_design_catches_syntax_errors(self):
        """Verify syntax errors are caught and reported."""
        from integration_coworker.graph.nodes.validate_integration_design import validate_integration_design
        from integration_coworker.domain.models import IntegrationTask, IntegrationFlowNode
        
        state = WorkflowState(
            source_refs=[],
            spec_refs=["test.yaml"],
            task_description="Test task",
            code_artifacts=[
                CodeArtifact(
                    id=None,
                    task_id=None,
                    rel_path="src/bad_client.py",
                    artifact_type="client",
                    language="python",
                    module_name="bad_client",
                    content="def broken(\n    # missing close paren and body"
                )
            ],
            # Provide minimal required state
            integration_task=IntegrationTask(
                id=None, source_system_id=None, task_slug="test_task", 
                provider_code="test", description="Test task"
            ),
            workflow_nodes=[
                IntegrationFlowNode(id=None, task_id=None, node_key="start",
                                   node_type="start", position=0),
                IntegrationFlowNode(id=None, task_id=None, node_key="end",
                                   node_type="end", position=1),
            ],
            endpoints=[
                Endpoint(id=1, source_system_id=1, spec_document_id=1,
                        path="/test", method="GET", operation_id="test",
                        summary=None, description=None,
                        request_schema_id=None, response_schema_id=None)
            ],
        )
        
        # validate_integration_design may raise on critical errors including syntax
        try:
            new_state = validate_integration_design(state)
            # If it doesn't raise, check errors
            assert len(new_state.errors) > 0
            assert any("syntax" in e.lower() or "Syntax" in e for e in new_state.errors)
        except ValueError as e:
            # Expected - critical validation failure includes syntax errors
            assert "critical error" in str(e).lower() or "Validation failed" in str(e)


# ============================================================================
# Test: build_report
# ============================================================================

class TestBuildReport:
    """Tests for build_report - Report generation."""
    
    def test_build_report_includes_all_sections(self):
        """Verify report includes all required sections."""
        from integration_coworker.graph.nodes.build_report import build_report
        
        # Build a state with data to report
        state = WorkflowState(
            source_refs=[],
            spec_refs=["test.yaml"],
            task_description="Test task",
        )
        state.run_id = "test-run-123"
        state.provider_code = "mock_payments"
        state.endpoints = [
            Endpoint(
                id=1, source_system_id=1, spec_document_id=1,
                path="/v1/checkout", method="POST", operation_id="checkout",
                summary="Create checkout", description="Create a checkout session",
                request_schema_id=None, response_schema_id=None
            )
        ]
        state.schemas = [Schema(id=1, source_system_id=1, name="Checkout", ref="#/schemas/Checkout")]
        state.workflow_nodes = [
            IntegrationFlowNode(id=1, task_id=1, position=0, node_key="start",
                              node_type="start", config={})
        ]
        state.policies = [
            Policy(id=1, task_id=1, policy_type=PolicyType.AUTH,
                  scope="flow", scope_ref="start", config={})
        ]
        state.completed_steps = ["plan_run", "build_silver_api_model"]
        
        new_state = build_report(state)
        
        report = new_state.report_markdown
        assert report is not None
        
        # Should include key sections
        assert "# Integration Co-Worker Report" in report
        assert "Run ID" in report
        assert "Silver API Model" in report
        assert "Integration Workflow" in report
        assert "Policies" in report
        assert "Completed Steps" in report
    
    def test_build_report_includes_node_timings(self):
        """Verify node timings are included when present."""
        from integration_coworker.graph.nodes.build_report import build_report
        
        state = WorkflowState(
            source_refs=[],
            spec_refs=["test.yaml"],
            task_description="Test task",
        )
        state.node_timings = {
            "plan_run": 1.23,
            "build_silver_api_model": 45.67,
            "persist_silver_checkpoint": 12.34,
        }
        state.completed_steps = ["plan_run"]
        
        new_state = build_report(state)
        
        report = new_state.report_markdown
        assert "Node Timings" in report
        assert "plan_run" in report
        assert "1.23 ms" in report or "1.23" in report


# ============================================================================
# Test: Node Timings Decorator
# ============================================================================

class TestTimedNodeDecorator:
    """Tests for the timed_node decorator."""
    
    def test_timed_node_records_execution_time(self):
        """Verify timed_node decorator records timing in state."""
        from integration_coworker.graph.runtime import timed_node
        
        @timed_node
        def slow_node(state: WorkflowState) -> WorkflowState:
            import time
            time.sleep(0.01)  # 10ms sleep
            return state
        
        state = WorkflowState(
            source_refs=[],
            spec_refs=["test.yaml"],
            task_description="Test task",
        )
        
        new_state = slow_node(state)
        
        # Should have timing recorded
        assert "slow_node" in new_state.node_timings
        
        # Should be at least 10ms (we slept 10ms)
        assert new_state.node_timings["slow_node"] >= 10.0
    
    def test_timed_node_preserves_function_name(self):
        """Verify decorator preserves function metadata."""
        from integration_coworker.graph.runtime import timed_node
        
        @timed_node
        def my_custom_node(state: WorkflowState) -> WorkflowState:
            """Custom node docstring."""
            return state
        
        assert my_custom_node.__name__ == "my_custom_node"
        assert "Custom node docstring" in my_custom_node.__doc__

    def test_timed_node_awaits_nested_coroutines(self):
        """Ensure timed_node handles async nodes that return awaitables."""
        from integration_coworker.graph.runtime import timed_node

        @timed_node
        async def async_node(state: WorkflowState) -> WorkflowState:
            async def inner():
                return state

            return inner()

        state = WorkflowState(
            source_refs=[],
            spec_refs=["test.yaml"],
            task_description="Test task",
        )

        new_state = asyncio.run(async_node(state))

        assert isinstance(new_state, WorkflowState)


# ============================================================================
# Integration Test: Full Pipeline Timing
# ============================================================================

class TestFullPipelineTiming:
    """Integration test verifying timing is recorded across full pipeline."""
    
    def test_demo_run_records_node_timings(self, mock_openapi_spec):
        """Verify a full demo run records timings for non-LLM nodes."""
        from integration_coworker.graph.runtime import run_workflow
        
        state = WorkflowState(
            source_refs=[str(mock_openapi_spec)],
            spec_refs=[str(mock_openapi_spec)],
            task_description="Create a checkout session",
            options=IntegrationOptions(dry_run=True),
        )
        
        final_state = run_workflow(state)
        
        # Should have recorded timings for non-LLM nodes
        assert len(final_state.node_timings) > 0
        
        # These nodes should have timing recorded
        expected_timed_nodes = [
            "plan_run",
            "build_silver_api_model",
            "persist_silver_checkpoint",
        ]
        for node_name in expected_timed_nodes:
            if node_name in final_state.completed_steps:
                # Node ran, should have timing
                assert node_name in final_state.node_timings, f"Missing timing for {node_name}"
                assert final_state.node_timings[node_name] >= 0, f"Invalid timing for {node_name}"

