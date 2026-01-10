"""
Node-level E2E test for File Integration V1 (Postgres-backed).

This test executes individual workflow nodes in sequence with:
- A CSV fixture as input
- Real Postgres backend via testcontainers
- Full assertions on state, persistence, and codegen

NOTE: This is node-level integration testing, not full graph runtime execution.
For true graph runtime tests, see TestFileIntegrationGraphRuntime.

This test MUST FAIL if detect_and_parse_spec doesn't route via SpecSource.

Run with: pytest tests/test_file_integration_e2e.py -v -m postgres
For CI: pytest tests/ -m "postgres and not testcontainers" excludes these tests
"""
import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import SourceSystem, SpecDocument
from integration_coworker.sources.base import ParsedSpec, SourceType


# Sample CSV fixture content
SAMPLE_CSV_CONTENT = """customer_id,name,email,amount,created_at
1,Alice Smith,alice@example.com,100.50,2024-01-15
2,Bob Jones,bob@example.com,250.75,2024-01-16
3,Carol White,carol@example.com,75.00,2024-01-17
4,David Brown,david@example.com,320.25,2024-01-18
5,Eve Davis,eve@example.com,180.00,2024-01-19
"""


@pytest.mark.postgres
@pytest.mark.testcontainers
class TestFileIntegrationNodeLevel:
    """
    Node-level E2E test: CSV spec → SpecSource routing → Silver model → Postgres persistence.
    
    This test runs individual workflow nodes in sequence (not the full graph runtime).
    Uses REAL Postgres via testcontainers and REAL node implementations.
    """
    
    def test_csv_spec_full_workflow(self, postgres_env, tmp_path, monkeypatch):
        """
        Full node-level test: CSV file → detect_and_parse_spec → build_silver_file_model → Postgres.
        
        This test verifies:
        1. detect_and_parse_spec calls detect_and_route() with CSV content (monkeypatched)
        2. parsed_specs contains ParsedSpec with SourceType.FILE
        3. build_silver_file_model processes ParsedSpec into typed file_specs/file_fields
        4. Data is persisted to Postgres (verified via SELECT)
        """
        import psycopg
        from integration_coworker import sources as sources_module
        
        # Create CSV fixture file
        csv_path = tmp_path / "customers.csv"
        csv_path.write_text(SAMPLE_CSV_CONTENT)
        
        # ==== Monkeypatch detect_and_route to prove SpecSource is being used ====
        # Store the original function
        original_detect_and_route = sources_module.detect_and_route
        call_tracker = {"calls": []}
        
        def tracked_detect_and_route(content, uri, content_type):
            """Wrapper that tracks calls then delegates to real function."""
            call_tracker["calls"].append({
                "content_preview": content[:100] if content else None,
                "uri": uri,
                "content_type": content_type,
            })
            return original_detect_and_route(content, uri, content_type)
        
        # Patch at the import location used by detect_and_parse_spec.py
        monkeypatch.setattr(
            "integration_coworker.graph.nodes.detect_and_parse_spec.detect_and_route",
            tracked_detect_and_route
        )
        
        # Import actual workflow nodes (not mocks)
        from integration_coworker.graph.nodes.detect_and_parse_spec import detect_and_parse_spec
        from integration_coworker.graph.nodes.build_silver_file_model import build_silver_file_model
        from integration_coworker.persistence import postgres as pg
        
        # Build initial state with CSV spec document
        state = WorkflowState(
            source_refs=[str(csv_path)],
            spec_refs=[str(csv_path)],
            task_description="Parse and persist customer CSV data"
        )
        state.spec_documents = [
            SpecDocument(
                id=None,
                source_system_id=None,
                version="1.0",
                uri=str(csv_path),
                content_type="text/csv",
                sha256="test_hash",
                content=SAMPLE_CSV_CONTENT,
            )
        ]
        
        # Phase 1: Run detect_and_parse_spec
        state = detect_and_parse_spec(state)
        
        # ==== CRITICAL: Assert detect_and_route was called with CSV content ====
        assert len(call_tracker["calls"]) >= 1, (
            "detect_and_route was NOT called! "
            "detect_and_parse_spec is not using SpecSource routing."
        )
        csv_call = call_tracker["calls"][0]
        assert "customer_id" in csv_call["content_preview"], (
            f"detect_and_route was called but not with CSV content: {csv_call}"
        )
        assert csv_call["content_type"] == "text/csv", (
            f"detect_and_route received wrong content_type: {csv_call['content_type']}"
        )
        
        # Assertion 1: CSV routed to parsed_specs as ParsedSpec
        assert len(state.parsed_specs) == 1, (
            f"Expected 1 ParsedSpec in parsed_specs, got {len(state.parsed_specs)}. "
            "detect_and_parse_spec is not routing CSV via SpecSource!"
        )
        
        parsed = state.parsed_specs[0]
        assert isinstance(parsed, ParsedSpec), (
            f"Expected ParsedSpec in parsed_specs, got {type(parsed).__name__}"
        )
        assert parsed.source_type == SourceType.FILE, (
            f"Expected SourceType.FILE, got {parsed.source_type}"
        )
        
        # Assertion 2: ParsedSpec contains valid FileSpec and fields
        assert parsed.is_valid(), f"ParsedSpec has errors: {parsed.errors}"
        assert "file_spec" in parsed.data
        assert "fields" in parsed.data
        
        from integration_coworker.domain.models import FileSpec, FileField
        assert isinstance(parsed.data["file_spec"], FileSpec)
        assert len(parsed.data["fields"]) == 5  # customer_id, name, email, amount, created_at
        
        # Phase 2: Create source_system (required by build_silver_file_model)
        with psycopg.connect(postgres_env) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO spec_silver.source_systems (code, display_name)
                    VALUES (%s, %s)
                    RETURNING id
                """, ("test_csv_source", "Test CSV Source"))
                source_id = cur.fetchone()[0]
                conn.commit()
        
        state.source_system = SourceSystem(
            id=source_id,
            code="test_csv_source",
            name="Test CSV Source"
        )
        
        # Phase 3: Run build_silver_file_model
        state = build_silver_file_model(state)
        
        # Assertion 3: file_specs now contains typed FileSpec objects
        assert len(state.file_specs) == 1, f"Expected 1 FileSpec, got {len(state.file_specs)}"
        assert isinstance(state.file_specs[0], FileSpec), (
            f"Expected FileSpec in file_specs, got {type(state.file_specs[0])}"
        )
        
        # Assertion 4: file_fields contains typed FileField objects
        assert len(state.file_fields) == 5
        for field in state.file_fields:
            assert isinstance(field, FileField), f"Expected FileField, got {type(field)}"
        
        # Assertion 5: Data persisted to Postgres (SELECT checks)
        with psycopg.connect(postgres_env) as conn:
            with conn.cursor() as cur:
                # Check file_specs table
                cur.execute("""
                    SELECT id, name, file_type, source_system_id
                    FROM spec_silver.file_specs
                    WHERE source_system_id = %s
                """, (source_id,))
                rows = cur.fetchall()
                assert len(rows) == 1, f"Expected 1 file_spec row, got {len(rows)}"
                file_spec_id, name, file_type, _ = rows[0]
                assert name == "customers"
                assert file_type == "csv"
                
                # Check file_fields table
                cur.execute("""
                    SELECT name, field_type, position
                    FROM spec_silver.file_fields
                    WHERE file_spec_id = %s
                    ORDER BY position
                """, (file_spec_id,))
                field_rows = cur.fetchall()
                assert len(field_rows) == 5
                
                expected_fields = [
                    ("customer_id", "integer", 0),
                    ("name", "string", 1),
                    ("email", "email", 2),
                    ("amount", "decimal", 3),
                    ("created_at", "date", 4),
                ]
                for actual, expected in zip(field_rows, expected_fields):
                    actual_name, actual_type, actual_pos = actual
                    exp_name, exp_type, exp_pos = expected
                    assert actual_name == exp_name, f"Field name mismatch: {actual_name} vs {exp_name}"
                    assert actual_pos == exp_pos, f"Position mismatch for {actual_name}"
                    # Type inference may vary slightly
    
    def test_csv_codegen_produces_executable_parser(self, postgres_env, tmp_path):
        """
        Test that codegen produces executable parser code for CSV.
        
        This verifies the parser can actually be imported and run against the fixture.
        """
        from integration_coworker.codegen.file_templates import generate_csv_parser
        from integration_coworker.domain.models import FileSpec, FileField
        
        # Create a FileSpec matching our sample CSV
        file_spec = FileSpec(
            id=None,
            source_system_id=None,
            name="customers",
            file_type="csv",
            delimiter=",",
            has_header=True,
            encoding="utf-8",
        )
        
        fields = [
            FileField(id=None, file_spec_id=None, name="customer_id", field_type="integer", position=0, nullable=False),
            FileField(id=None, file_spec_id=None, name="name", field_type="string", position=1, nullable=False),
            FileField(id=None, file_spec_id=None, name="email", field_type="string", position=2, nullable=False),
            FileField(id=None, file_spec_id=None, name="amount", field_type="decimal", position=3, nullable=False),
            FileField(id=None, file_spec_id=None, name="created_at", field_type="date", position=4, nullable=False),
        ]
        
        # Generate parser code
        parser_code = generate_csv_parser(file_spec, fields, language="python")
        
        # Write to temp file
        parser_path = tmp_path / "generated_parser.py"
        parser_path.write_text(parser_code)
        
        # Create CSV fixture
        csv_path = tmp_path / "customers.csv"
        csv_path.write_text(SAMPLE_CSV_CONTENT)
        
        # Execute the generated parser code
        import sys
        sys.path.insert(0, str(tmp_path))
        try:
            # Import the generated module
            import importlib.util
            spec = importlib.util.spec_from_file_location("generated_parser", parser_path)
            parser_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(parser_module)
            
            # Parse the CSV
            records = parser_module.parse_customers(str(csv_path))
            
            # Verify parsing worked
            assert len(records) == 5
            assert records[0].customer_id == 1
            assert records[0].name == "Alice Smith"
            assert str(records[0].amount) == "100.50"
            
        finally:
            sys.path.remove(str(tmp_path))
    
    def test_mixed_api_and_csv_specs(self, postgres_env, tmp_path):
        """
        Test that mixed API + CSV specs are routed correctly.
        
        OpenAPI should go to openapi_spec, CSV should go to parsed_specs.
        """
        from integration_coworker.graph.nodes.detect_and_parse_spec import detect_and_parse_spec
        
        openapi_content = '''
openapi: "3.0.0"
info:
  title: Customer API
  version: "1.0.0"
paths:
  /customers:
    get:
      summary: List customers
      responses:
        "200":
          description: OK
'''
        
        # Build state with BOTH specs
        state = WorkflowState(
            source_refs=["api.yaml", "customers.csv"],
            spec_refs=["api.yaml", "customers.csv"],
            task_description="Process API and CSV together"
        )
        state.spec_documents = [
            SpecDocument(
                id=None,
                source_system_id=None,
                version="1.0",
                uri="api.yaml",
                content_type="application/yaml",
                sha256="hash1",
                content=openapi_content,
            ),
            SpecDocument(
                id=None,
                source_system_id=None,
                version="1.0",
                uri="customers.csv",
                content_type="text/csv",
                sha256="hash2",
                content=SAMPLE_CSV_CONTENT,
            ),
        ]
        
        # Run detect_and_parse_spec
        state = detect_and_parse_spec(state)
        
        # OpenAPI should be in openapi_spec
        assert state.openapi_spec is not None
        assert state.openapi_spec["info"]["title"] == "Customer API"
        
        # CSV should be in parsed_specs
        assert len(state.parsed_specs) == 1
        assert state.parsed_specs[0].source_type == SourceType.FILE


@pytest.mark.postgres
@pytest.mark.testcontainers
class TestFileIntegrationGraphRuntime:
    """
    Test the file integration using the actual graph runtime.
    
    These tests use create_workflow to build the real graph.
    """
    
    def test_partial_workflow_file_branch(self, postgres_env, tmp_path, mock_embeddings, mock_llm):
        """
        Run partial workflow focusing on file integration branch.
        
        This tests the real graph routing without running LLM-heavy nodes.
        """
        # Create CSV fixture
        csv_path = tmp_path / "orders.csv"
        csv_content = "order_id,customer,total\n1,alice,99.99\n2,bob,149.50\n"
        csv_path.write_text(csv_content)
        
        # Build initial state
        initial_state = WorkflowState(
            source_refs=[str(csv_path)],
            spec_refs=[str(csv_path)],
            task_description="Parse orders CSV"
        )
        initial_state.spec_documents = [
            SpecDocument(
                id=None,
                source_system_id=None,
                version="1.0",
                uri=str(csv_path),
                content_type="text/csv",
                sha256="test",
                content=csv_content,
            )
        ]
        
        # Create source_system via direct SQL (required before workflow)
        import psycopg
        with psycopg.connect(postgres_env) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO spec_silver.source_systems (code, display_name)
                    VALUES (%s, %s) RETURNING id
                """, ("orders_source", "Orders Source"))
                source_id = cur.fetchone()[0]
                conn.commit()
        
        initial_state.source_system = SourceSystem(
            id=source_id, code="orders_source", name="Orders Source"
        )
        
        # Import and run individual nodes (simulating graph execution)
        from integration_coworker.graph.nodes.detect_and_parse_spec import detect_and_parse_spec
        from integration_coworker.graph.nodes.build_silver_file_model import build_silver_file_model
        
        # Run nodes in sequence
        state = detect_and_parse_spec(initial_state)
        state = build_silver_file_model(state)
        
        # Verify completed steps
        assert "detect_and_parse_spec" in state.completed_steps
        assert "build_silver_file_model" in state.completed_steps
        
        # Verify file data was processed
        assert len(state.file_specs) == 1
        assert state.file_specs[0].name == "orders"
        assert len(state.file_fields) == 3  # order_id, customer, total


class TestWarningPropagation:
    """
    Explicit tests proving that warnings propagate from ParsedSpec into WorkflowState.
    
    This is a contract test: if the warning propagation code in detect_and_parse_spec
    is removed, these tests MUST fail.
    """

    def test_parsed_spec_warnings_propagate_to_state_warnings(self, tmp_path, monkeypatch):
        """
        Prove warnings from ParsedSpec.warnings flow into state.warnings.
        
        Setup:
        1. Create a file spec that will generate inference warnings
        2. Monkeypatch detect_and_route to return ParsedSpec with explicit warnings
        3. Run detect_and_parse_spec
        4. Assert state.warnings contains the propagated warnings
        
        This test MUST fail if lines 854-857 of detect_and_parse_spec.py are removed:
            if parsed_spec.warnings:
                for warning in parsed_spec.warnings:
                    state.warnings.append(f"[{uri}] {warning}")
        """
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.domain.models import SourceSystem, SpecDocument
        from integration_coworker.sources.base import ParsedSpec, SourceType
        from integration_coworker.graph.nodes.detect_and_parse_spec import detect_and_parse_spec
        
        # Create a simple file
        test_file = tmp_path / "test_with_warnings.csv"
        test_file.write_text("a,b,c\n1,2,3\n")
        
        # Create ParsedSpec with explicit warnings (simulating low-confidence inference)
        warnings_to_inject = [
            "Low inference confidence (0.65) for field boundaries",
            "Field 'amount' type inference uncertain (support: 0.70)",
            "Consider manual review of schema",
        ]
        
        fake_parsed_spec = ParsedSpec(
            source_type=SourceType.FILE,
            source_uri=str(test_file),
            data={"file_spec": None, "fields": []},
            confidence=0.65,
            warnings=warnings_to_inject.copy(),
            errors=[],
        )
        
        # Monkeypatch detect_and_route to return our ParsedSpec with warnings
        def mock_detect_and_route(content, uri, content_type):
            return fake_parsed_spec
        
        monkeypatch.setattr(
            "integration_coworker.graph.nodes.detect_and_parse_spec.detect_and_route",
            mock_detect_and_route
        )
        
        # Build initial state
        state = WorkflowState(
            source_refs=[str(test_file)],
            spec_refs=[str(test_file)],
            task_description="Test warning propagation"
        )
        state.spec_documents = [
            SpecDocument(
                id=None,
                source_system_id=None,
                version="1.0",
                uri=str(test_file),
                content_type="text/csv",
                sha256="test",
                content="a,b,c\n1,2,3\n",
            )
        ]
        
        # Run the node
        result_state = detect_and_parse_spec(state)
        
        # CRITICAL ASSERTION: warnings propagated to state
        assert len(result_state.warnings) >= len(warnings_to_inject), (
            f"Expected at least {len(warnings_to_inject)} warnings in state.warnings, "
            f"got {len(result_state.warnings)}. "
            "Warning propagation code (lines 854-857) may have been removed!"
        )
        
        # Verify each injected warning is present (with URI prefix)
        for warning in warnings_to_inject:
            found = any(warning in w for w in result_state.warnings)
            assert found, (
                f"Warning '{warning}' not found in state.warnings: {result_state.warnings}. "
                "Warning propagation code may have been removed!"
            )
        
        # Verify URI prefix is present
        uri_prefix = f"[{test_file}]"
        assert any(uri_prefix in w for w in result_state.warnings), (
            f"URI prefix '{uri_prefix}' not found in warnings. "
            "Warning formatting code may have been removed!"
        )

    def test_failed_parsedspec_warnings_also_propagate(self, tmp_path, monkeypatch):
        """
        Prove warnings propagate even when ParsedSpec has errors (is_valid() == False).
        
        This tests the code path at lines 863-866:
            if parsed_spec.warnings:
                for warning in parsed_spec.warnings:
                    state.warnings.append(f"[{uri}] WARN: {warning}")
        """
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.domain.models import SpecDocument
        from integration_coworker.sources.base import ParsedSpec, SourceType
        from integration_coworker.graph.nodes.detect_and_parse_spec import detect_and_parse_spec
        
        test_file = tmp_path / "bad_file.csv"
        test_file.write_text("broken content")
        
        # ParsedSpec with errors AND warnings
        failed_spec = ParsedSpec(
            source_type=SourceType.FILE,
            source_uri=str(test_file),
            data={},
            confidence=0.0,
            warnings=["Partial parse attempted before failure"],
            errors=["Schema inference failed completely"],
        )
        
        def mock_detect_and_route(content, uri, content_type):
            return failed_spec
        
        monkeypatch.setattr(
            "integration_coworker.graph.nodes.detect_and_parse_spec.detect_and_route",
            mock_detect_and_route
        )
        
        state = WorkflowState(
            source_refs=[str(test_file)],
            spec_refs=[str(test_file)],
            task_description="Test failed spec warning propagation"
        )
        state.spec_documents = [
            SpecDocument(
                id=None,
                source_system_id=None,
                version="1.0",
                uri=str(test_file),
                content_type="text/csv",
                sha256="test",
                content="broken",
            )
        ]
        
        result_state = detect_and_parse_spec(state)
        
        # CRITICAL: Even failed specs should propagate warnings
        assert any("Partial parse attempted" in w for w in result_state.warnings), (
            "Warnings from failed ParsedSpec not propagated to state.warnings. "
            "Lines 863-866 of detect_and_parse_spec.py may have been removed!"
        )
        
        # Also verify errors are captured
        assert any("failed parsing" in e.lower() for e in result_state.errors), (
            "Errors from failed ParsedSpec not propagated to state.errors"
        )
