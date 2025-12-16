"""
Trusted Demo Scenarios - Canonical integration tests for V1.

These tests validate the complete integration workflow for key use cases.
Each scenario is designed to:
1. Exercise the full graph with mock LLM
2. Assert on critical outputs: workflow_nodes, endpoint_bindings, code_artifacts
3. Generate snapshot artifacts for regression detection

Reference: docs/V1_COMPLETION_SUMMARY.md Section "Trusted Scenarios"

Scenarios:
- Scenario 1: Single-endpoint (Create payment)
- Scenario 2: Multi-endpoint (Create order + send confirmation)
- Scenario 3: Multi-spec ingestion (Payments + Notifications)
- Scenario 4: HTML/PDF spec fallback (text-based parsing)
"""
import json
import os
import pytest
import tempfile
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict

pytestmark = pytest.mark.requires_aiosqlite

from integration_coworker.api.entrypoint import design_and_generate_integration
from integration_coworker.api.types import IntegrationOptions, IntegrationResult
from integration_coworker.repo.profiles import SUBATOMIC_MOCK_PROFILE


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def fixtures_dir() -> Path:
    """Return the fixtures directory path."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def snapshots_dir() -> Path:
    """Return the snapshots directory, creating if needed."""
    snap_dir = Path(__file__).parent / "snapshots"
    snap_dir.mkdir(exist_ok=True)
    return snap_dir


@pytest.fixture
def mock_payments_spec(fixtures_dir) -> str:
    """Path to mock payments OpenAPI spec."""
    return str(fixtures_dir / "mock_payments_openapi.yaml")


@pytest.fixture
def mock_notifications_spec(fixtures_dir) -> str:
    """Path to mock notifications OpenAPI spec."""
    return str(fixtures_dir / "mock_notifications_openapi.yaml")


@pytest.fixture
def temp_repo():
    """Create a temporary repo directory for file writes."""
    temp_dir = tempfile.mkdtemp(prefix="trusted_demo_")
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


# ==============================================================================
# Snapshot Utilities
# ==============================================================================

def _result_to_snapshot(result: IntegrationResult) -> Dict[str, Any]:
    """
    Convert IntegrationResult to a minimal, stable snapshot dict.
    
    Excludes volatile fields like run_id, timestamps, full code content.
    """
    snapshot = {
        "task": {
            "task_slug": result.task.task_slug if result.task else None,
            "provider_code": result.task.provider_code if result.task else None,
        },
        "endpoints_count": len(result.endpoints),
        "endpoint_methods": sorted([
            f"{ep.method} {ep.path}" for ep in result.endpoints
        ]),
        "schemas_count": len(result.schemas),
        "code_artifacts": sorted([
            {
                "type": a.artifact_type,
                "module": a.module_name,
                "has_content": len(a.content) > 0,
            }
            for a in result.code_artifacts
        ], key=lambda x: x["type"]),
        "workflow_nodes_count": len(result.workflow_nodes),
        "endpoint_bindings_count": len(result.endpoint_bindings),
        "completed_steps": sorted(result.completed_steps),
        "has_report": len(result.report_markdown) > 0,
        "errors": result.errors,
    }
    
    # Add node keys if available
    if result.workflow_nodes:
        snapshot["workflow_node_keys"] = sorted([
            n.key if hasattr(n, 'key') else str(n)
            for n in result.workflow_nodes
        ])
    
    # Add binding info if available
    if result.endpoint_bindings:
        snapshot["binding_endpoints"] = sorted([
            f"{b.endpoint_id}" if hasattr(b, 'endpoint_id') else str(b)
            for b in result.endpoint_bindings
        ])
    
    return snapshot


def _save_snapshot(snapshots_dir: Path, name: str, data: Dict[str, Any]):
    """Save snapshot to JSON file for human review."""
    snapshot_path = snapshots_dir / f"{name}.json"
    with open(snapshot_path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def _load_snapshot(snapshots_dir: Path, name: str) -> Dict[str, Any] | None:
    """Load existing snapshot if present."""
    snapshot_path = snapshots_dir / f"{name}.json"
    if snapshot_path.exists():
        with open(snapshot_path) as f:
            return json.load(f)
    return None


# ==============================================================================
# Scenario 1: Single-Endpoint (Create Payment)
# ==============================================================================

class TestScenario1SingleEndpoint:
    """
    Scenario: Simple single-endpoint integration.
    
    Task: "Create checkout session" against mock_payments API.
    Expected: Single endpoint bound, client/flow/test generated.
    """
    
    SCENARIO_NAME = "scenario1_single_endpoint"
    
    def test_single_endpoint_dry_run(self, mock_payments_spec, snapshots_dir):
        """Run single-endpoint scenario and verify outputs."""
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec],
            task_description="Create checkout session",
            provider_code="mock_payments",
            options=IntegrationOptions(
                dry_run=True,
                repo_integration_enabled=False,
            ),
        )
        
        # Core assertions
        assert result.run_id is not None
        assert result.task is not None
        assert result.task.task_slug == "create_checkout_session"
        assert result.task.provider_code == "mock_payments"
        
        # Endpoints extracted
        assert len(result.endpoints) >= 1
        post_endpoints = [ep for ep in result.endpoints if ep.method == "POST"]
        assert len(post_endpoints) >= 1
        
        # Code artifacts generated (client, flow, test)
        assert len(result.code_artifacts) == 3
        artifact_types = {a.artifact_type for a in result.code_artifacts}
        assert artifact_types == {"client", "flow", "test"}
        
        # All artifacts have content
        for artifact in result.code_artifacts:
            assert len(artifact.content) > 0, f"Empty {artifact.artifact_type}"
        
        # Report generated
        assert len(result.report_markdown) > 0
        assert "mock_payments" in result.report_markdown.lower()
        
        # Workflow completed
        assert "generate_code_and_tests" in result.completed_steps
        assert "build_report" in result.completed_steps
        
        # Save snapshot
        snapshot = _result_to_snapshot(result)
        _save_snapshot(snapshots_dir, self.SCENARIO_NAME, snapshot)
        
        # Verify no critical errors (warnings about endpoint_id=None are OK)
        critical_errors = [e for e in result.errors if "Warning:" not in e]
        assert len(critical_errors) == 0, f"Critical errors: {critical_errors}"
    
    def test_single_endpoint_node_timings(self, mock_payments_spec):
        """Verify node timing data is captured in report."""
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec],
            task_description="Create checkout session",
            provider_code="mock_payments",
            options=IntegrationOptions(
                dry_run=True,
                repo_integration_enabled=False,
            ),
        )
        
        # Check report has node timings section
        assert result.report_markdown is not None
        assert "Node Timings" in result.report_markdown
        
        # Key nodes should appear in timings section
        expected_nodes = [
            "plan_run",
            "ingest_spec",
            "detect_and_parse_spec",
            "build_silver_api_model",
        ]
        for node in expected_nodes:
            assert node in result.report_markdown, f"Missing timing for {node}"


# ==============================================================================
# Scenario 2: Multi-Endpoint (Order + Notification)
# ==============================================================================

class TestScenario2MultiEndpoint:
    """
    Scenario: Multi-endpoint workflow.
    
    Task: "Create order and send confirmation email" using payments + notifications.
    Expected: Multiple endpoints bound in sequence, multi-call flow generated.
    """
    
    SCENARIO_NAME = "scenario2_multi_endpoint"
    
    def test_multi_endpoint_dry_run(
        self, mock_payments_spec, mock_notifications_spec, snapshots_dir
    ):
        """Run multi-endpoint scenario with two specs."""
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec, mock_notifications_spec],
            task_description="Create checkout session and send confirmation notification",
            options=IntegrationOptions(
                dry_run=True,
                repo_integration_enabled=False,
            ),
        )
        
        # Core assertions
        assert result.run_id is not None
        assert result.task is not None
        
        # Both specs ingested
        assert len(result.spec_documents) == 2
        
        # Endpoints from both specs extracted
        assert len(result.endpoints) >= 3  # At least some from each
        
        endpoint_paths = {ep.path for ep in result.endpoints}
        # Should have paths from both APIs
        has_payments = any("/checkout" in p or "/payment" in p for p in endpoint_paths)
        has_notifications = any("/notification" in p for p in endpoint_paths)
        assert has_payments, f"No payment endpoints in {endpoint_paths}"
        assert has_notifications, f"No notification endpoints in {endpoint_paths}"
        
        # Code artifacts generated
        assert len(result.code_artifacts) >= 3
        
        # Workflow should recognize multi-step nature
        # (The exact behavior depends on align_task_with_kg multi-endpoint detection)
        
        # Save snapshot
        snapshot = _result_to_snapshot(result)
        _save_snapshot(snapshots_dir, self.SCENARIO_NAME, snapshot)
        
        # Report mentions both concerns
        report_lower = result.report_markdown.lower()
        assert "checkout" in report_lower or "payment" in report_lower
    
    def test_multi_endpoint_bindings(
        self, mock_payments_spec, mock_notifications_spec
    ):
        """Verify endpoint bindings are created for multi-endpoint flow."""
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec, mock_notifications_spec],
            task_description="Create checkout session and send notification",
            options=IntegrationOptions(
                dry_run=True,
                repo_integration_enabled=False,
            ),
        )
        
        # Should have endpoint bindings
        # Note: exact count depends on KG alignment and multi-call detection
        assert len(result.endpoint_bindings) >= 1
        
        # Each binding should have an endpoint reference
        for binding in result.endpoint_bindings:
            assert hasattr(binding, 'endpoint_id') or hasattr(binding, 'endpoint')


# ==============================================================================
# Scenario 3: Multi-Spec Ingestion (Detailed)
# ==============================================================================

class TestScenario3MultiSpec:
    """
    Scenario: Multi-spec ingestion verification.
    
    Verifies that both specs are fully parsed and their entities extracted.
    """
    
    SCENARIO_NAME = "scenario3_multi_spec"
    
    def test_multi_spec_extraction(
        self, mock_payments_spec, mock_notifications_spec, snapshots_dir
    ):
        """Verify all endpoints and schemas from both specs are extracted."""
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec, mock_notifications_spec],
            task_description="Process payments with notifications",
            options=IntegrationOptions(
                dry_run=True,
                repo_integration_enabled=False,
            ),
        )
        
        # Both specs ingested
        assert len(result.spec_documents) == 2
        spec_uris = {doc.uri for doc in result.spec_documents}
        assert mock_payments_spec in spec_uris
        assert mock_notifications_spec in spec_uris
        
        # All endpoints extracted:
        # - mock_payments: POST /v1/checkout/sessions, GET /v1/checkout/sessions/{id}
        # - mock_notifications: POST /v1/notifications, GET /v1/notifications/{id}
        assert len(result.endpoints) == 4
        
        endpoint_signatures = {(ep.method, ep.path) for ep in result.endpoints}
        expected = {
            ("POST", "/v1/checkout/sessions"),
            ("GET", "/v1/checkout/sessions/{id}"),
            ("POST", "/v1/notifications"),
            ("GET", "/v1/notifications/{id}"),
        }
        assert endpoint_signatures == expected
        
        # Schemas extracted from both
        assert len(result.schemas) >= 4  # At least request/response from each
        
        # Save snapshot
        snapshot = _result_to_snapshot(result)
        _save_snapshot(snapshots_dir, self.SCENARIO_NAME, snapshot)


# ==============================================================================
# Scenario 4: Repo Integration (File Writes)
# ==============================================================================

class TestScenario4RepoIntegration:
    """
    Scenario: Full repo integration with file writes.
    
    Tests that generated code is properly written to a target repo.
    """
    
    SCENARIO_NAME = "scenario4_repo_integration"
    
    def test_repo_file_writes(self, mock_payments_spec, temp_repo, snapshots_dir):
        """Verify files are created in target repo."""
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec],
            task_description="Create checkout session",
            provider_code="mock_payments",
            repo_root=str(temp_repo),
            repo_profile=SUBATOMIC_MOCK_PROFILE,
            options=IntegrationOptions(
                dry_run=False,
                repo_integration_enabled=True,
            ),
        )
        
        # Repo changes recorded
        assert result.repo_changes is not None
        assert len(result.repo_changes.changes) >= 3  # client, flow, test at minimum
        
        # Files actually created
        created_files = result.repo_changes.files_created()
        assert len(created_files) >= 3
        
        for change in created_files:
            file_path = temp_repo / change.rel_path
            assert file_path.exists(), f"File not created: {change.rel_path}"
            content = file_path.read_text()
            assert len(content) > 0, f"Empty file: {change.rel_path}"
        
        # Client file has expected content
        client_files = [c for c in created_files if "client" in c.rel_path]
        assert len(client_files) == 1
        client_content = (temp_repo / client_files[0].rel_path).read_text()
        assert "class" in client_content  # Has a class definition
        assert "def" in client_content    # Has method definitions
        
        # Save snapshot (excluding actual file contents)
        snapshot = _result_to_snapshot(result)
        snapshot["files_created"] = sorted([c.rel_path for c in created_files])
        _save_snapshot(snapshots_dir, self.SCENARIO_NAME, snapshot)
    
    def test_repo_dry_run_no_writes(self, mock_payments_spec, temp_repo):
        """Verify dry_run=True doesn't write files."""
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec],
            task_description="Create checkout session",
            provider_code="mock_payments",
            repo_root=str(temp_repo),
            repo_profile=SUBATOMIC_MOCK_PROFILE,
            options=IntegrationOptions(
                dry_run=True,
                repo_integration_enabled=True,
            ),
        )
        
        # Plan should have dry_run info
        assert result.plan is not None
        
        # No files should be created on disk
        created_count = sum(1 for _ in temp_repo.iterdir())
        # Only empty dir or backup dir might exist
        assert created_count <= 1


# ==============================================================================
# Scenario 5: KG Alignment Verification
# ==============================================================================

class TestScenario5KGAlignment:
    """
    Scenario: Knowledge graph alignment verification.
    
    Tests that the align_task_with_kg node produces expected template matches.
    """
    
    SCENARIO_NAME = "scenario5_kg_alignment"
    
    def test_kg_alignment_produces_template(self, mock_payments_spec, snapshots_dir):
        """Verify KG alignment selects appropriate workflow template."""
        result = design_and_generate_integration(
            spec_refs=[mock_payments_spec],
            task_description="Create checkout session",
            provider_code="mock_payments",
            options=IntegrationOptions(
                dry_run=True,
                repo_integration_enabled=False,
            ),
        )
        
        # Workflow nodes should be populated
        assert len(result.workflow_nodes) >= 1
        
        # Should have completed align_task_with_kg
        assert "align_task_with_kg" in result.completed_steps
        
        # Plan should have kg_alignment info
        assert result.plan is not None
        if "kg_alignment" in result.plan:
            kg_info = result.plan["kg_alignment"]
            # Should have alignment details
            assert isinstance(kg_info, dict)
        
        # Save snapshot
        snapshot = _result_to_snapshot(result)
        if result.plan and "kg_alignment" in result.plan:
            snapshot["kg_alignment"] = result.plan["kg_alignment"]
        _save_snapshot(snapshots_dir, self.SCENARIO_NAME, snapshot)


# ==============================================================================
# Summary Test (Smoke)
# ==============================================================================

class TestTrustedScenariosSmoke:
    """Quick smoke test that all scenarios complete without error."""
    
    def test_all_scenarios_complete(
        self, mock_payments_spec, mock_notifications_spec
    ):
        """Verify all canonical scenarios complete successfully."""
        scenarios = [
            # (spec_refs, task_description, name)
            (
                [mock_payments_spec],
                "Create checkout session",
                "single_endpoint",
            ),
            (
                [mock_payments_spec, mock_notifications_spec],
                "Create checkout and send notification",
                "multi_endpoint",
            ),
        ]
        
        for spec_refs, task, name in scenarios:
            result = design_and_generate_integration(
                spec_refs=spec_refs,
                task_description=task,
                options=IntegrationOptions(
                    dry_run=True,
                    repo_integration_enabled=False,
                ),
            )
            
            assert result.run_id is not None, f"Failed: {name}"
            # Only fail on critical errors, not warnings
            critical_errors = [e for e in result.errors if "Warning:" not in e]
            assert len(critical_errors) == 0, f"Errors in {name}: {critical_errors}"
            assert len(result.code_artifacts) >= 3, f"Missing artifacts in {name}"
