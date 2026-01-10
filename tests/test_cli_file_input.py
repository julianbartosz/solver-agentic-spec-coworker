"""
Tests for CLI file input handling (Step 8/8 of FILE_INTEGRATION_PRODUCTION_PLAN).

These tests verify:
1. CLI accepts --file and --guide options
2. Exit code 2 for usage errors (missing input, nonexistent files)
3. Exit code 1 for runtime errors
4. Combined spec_refs + file inputs work correctly

Per Click/Typer convention:
- Exit 0: Success
- Exit 1: Runtime error
- Exit 2: Usage/argument error
"""
import os
import pytest
import tempfile
from pathlib import Path
from typer.testing import CliRunner
from unittest.mock import patch, MagicMock

from integration_coworker.cli import app


runner = CliRunner()


# Mark all tests as not needing DB and enabling sockets for typer testing
pytestmark = [
    pytest.mark.no_db,
    pytest.mark.enable_socket,
]


@pytest.fixture(autouse=True)
def hermetic_cli_env(monkeypatch):
    """
    Make CLI tests hermetic by isolating environment variables.
    
    Without this, tests would depend on external shell env vars,
    causing flaky failures in different environments.
    """
    # Ensure offline validation profile - no network calls
    monkeypatch.setenv("VALIDATION_PROFILE", "offline")
    # Use mock LLM to avoid API key requirements
    monkeypatch.setenv("USE_MOCK_LLM", "true")
    # Isolate database to temp file
    monkeypatch.setenv("BEADS_DB", "/tmp/test_cli_file_input.db")
    yield


class TestCLIInputValidation:
    """Tests for CLI input validation (exit code 2 cases)."""
    
    def test_run_missing_input_exits_2(self):
        """CLI exits 2 when neither --spec-ref, --file, nor --guide provided."""
        result = runner.invoke(app, ["run", "--task", "Parse some data"])
        assert result.exit_code == 2, f"Expected exit 2, got {result.exit_code}. Output: {result.output}"
        assert "Must provide at least one --spec-ref, --file, or --guide" in result.output
    
    def test_run_nonexistent_file_exits_2(self):
        """CLI exits 2 for nonexistent --file path."""
        result = runner.invoke(app, [
            "run",
            "--file", "/does/not/exist/transactions.csv",
            "--task", "Parse transactions"
        ])
        assert result.exit_code == 2, f"Expected exit 2, got {result.exit_code}. Output: {result.output}"
        assert "File not found" in result.output
    
    def test_run_nonexistent_guide_exits_2(self):
        """CLI exits 2 for nonexistent --guide path."""
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            f.write(b"name,value\nfoo,1\n")
            csv_path = f.name
        
        try:
            result = runner.invoke(app, [
                "run",
                "--file", csv_path,
                "--guide", "/does/not/exist/layout.pdf",
                "--task", "Parse fixed-width"
            ])
            assert result.exit_code == 2, f"Expected exit 2, got {result.exit_code}. Output: {result.output}"
            assert "Guide file not found" in result.output
        finally:
            os.unlink(csv_path)
    
    def test_file_option_accepts_csv(self):
        """CLI accepts .csv file via --file option."""
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            f.write(b"name,value\nfoo,1\n")
            csv_path = f.name
        
        try:
            # We mock the actual workflow to avoid full run, just test option parsing
            with patch('integration_coworker.cli.design_and_generate_integration') as mock_run:
                mock_result = MagicMock()
                mock_result.run_id = "test-run-123"
                mock_result.task = None
                mock_result.code_artifacts = []
                mock_result.repo_changes = None
                mock_result.report_markdown = "## Success"
                mock_run.return_value = mock_result
                
                result = runner.invoke(app, [
                    "run",
                    "--file", csv_path,
                    "--task", "Parse CSV",
                    "--dry-run"
                ])
                
                # Check that the call was made with the file in spec_refs
                assert mock_run.called, "design_and_generate_integration should be called"
                call_kwargs = mock_run.call_args[1] if mock_run.call_args[1] else {}
                call_args = mock_run.call_args[0] if mock_run.call_args[0] else ()
                
                # spec_refs should be a keyword arg
                if 'spec_refs' in call_kwargs:
                    spec_refs = call_kwargs['spec_refs']
                else:
                    # First positional arg
                    spec_refs = call_args[0] if call_args else []
                
                assert any(csv_path in ref or Path(csv_path).resolve().as_posix() in ref 
                          for ref in spec_refs), f"CSV path not in spec_refs: {spec_refs}"
        finally:
            os.unlink(csv_path)
    
    def test_file_option_accepts_excel(self):
        """CLI accepts .xlsx file via --file option."""
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            # Write minimal xlsx bytes (not valid Excel, but enough for path checking)
            f.write(b"PK")  # Excel files are ZIP-based
            xlsx_path = f.name
        
        try:
            with patch('integration_coworker.cli.design_and_generate_integration') as mock_run:
                mock_result = MagicMock()
                mock_result.run_id = "test-run-123"
                mock_result.task = None
                mock_result.code_artifacts = []
                mock_result.repo_changes = None
                mock_result.report_markdown = "## Success"
                mock_run.return_value = mock_result
                
                result = runner.invoke(app, [
                    "run",
                    "--file", xlsx_path,
                    "--task", "Parse Excel",
                    "--dry-run"
                ])
                
                assert mock_run.called, "design_and_generate_integration should be called"
        finally:
            os.unlink(xlsx_path)
    
    def test_multiple_file_inputs(self):
        """CLI accepts multiple --file options."""
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f1:
            f1.write(b"name,value\nfoo,1\n")
            csv1_path = f1.name
        
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f2:
            f2.write(b"id,amount\n1,100\n")
            csv2_path = f2.name
        
        try:
            with patch('integration_coworker.cli.design_and_generate_integration') as mock_run:
                mock_result = MagicMock()
                mock_result.run_id = "test-run-123"
                mock_result.task = None
                mock_result.code_artifacts = []
                mock_result.repo_changes = None
                mock_result.report_markdown = "## Success"
                mock_run.return_value = mock_result
                
                result = runner.invoke(app, [
                    "run",
                    "--file", csv1_path,
                    "--file", csv2_path,
                    "--task", "Parse multiple files",
                    "--dry-run"
                ])
                
                assert mock_run.called
                call_kwargs = mock_run.call_args[1] if mock_run.call_args[1] else {}
                spec_refs = call_kwargs.get('spec_refs', [])
                
                # Both files should be in spec_refs
                assert len(spec_refs) >= 2, f"Expected at least 2 refs, got {spec_refs}"
        finally:
            os.unlink(csv1_path)
            os.unlink(csv2_path)
    
    def test_mixed_spec_and_file_inputs(self):
        """CLI accepts both --spec-ref and --file options together."""
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as spec_file:
            spec_file.write(b"openapi: '3.0.0'\ninfo:\n  title: Test\n  version: '1.0'\npaths: {}\n")
            spec_path = spec_file.name
        
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as data_file:
            data_file.write(b"name,value\nfoo,1\n")
            csv_path = data_file.name
        
        try:
            with patch('integration_coworker.cli.design_and_generate_integration') as mock_run:
                mock_result = MagicMock()
                mock_result.run_id = "test-run-123"
                mock_result.task = None
                mock_result.code_artifacts = []
                mock_result.repo_changes = None
                mock_result.report_markdown = "## Success"
                mock_run.return_value = mock_result
                
                result = runner.invoke(app, [
                    "run",
                    "--spec-ref", spec_path,
                    "--file", csv_path,
                    "--task", "Mixed API and file",
                    "--dry-run"
                ])
                
                assert mock_run.called
                call_kwargs = mock_run.call_args[1] if mock_run.call_args[1] else {}
                spec_refs = call_kwargs.get('spec_refs', [])
                
                # Both spec and file should be in spec_refs
                assert len(spec_refs) == 2, f"Expected 2 refs, got {spec_refs}"
        finally:
            os.unlink(spec_path)
            os.unlink(csv_path)


class TestCLIWithGuide:
    """Tests for --guide option (PDF layout specs for fixed-width files)."""
    
    def test_guide_without_file_accepted(self):
        """CLI accepts --guide alone (guide is a spec source too)."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as guide_file:
            # Minimal PDF header
            guide_file.write(b"%PDF-1.4\n")
            guide_path = guide_file.name
        
        try:
            with patch('integration_coworker.cli.design_and_generate_integration') as mock_run:
                mock_result = MagicMock()
                mock_result.run_id = "test-run-123"
                mock_result.task = None
                mock_result.code_artifacts = []
                mock_result.repo_changes = None
                mock_result.report_markdown = "## Success"
                mock_run.return_value = mock_result
                
                result = runner.invoke(app, [
                    "run",
                    "--guide", guide_path,
                    "--task", "Parse fixed-width from guide",
                    "--dry-run"
                ])
                
                assert mock_run.called
        finally:
            os.unlink(guide_path)
    
    def test_guide_with_data_file(self):
        """CLI accepts --guide together with --file."""
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as data_file:
            # Fixed-width sample
            data_file.write(b"001ALICE    000100\n002BOB      000050\n")
            data_path = data_file.name
        
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as guide_file:
            guide_file.write(b"%PDF-1.4\n")
            guide_path = guide_file.name
        
        try:
            with patch('integration_coworker.cli.design_and_generate_integration') as mock_run:
                mock_result = MagicMock()
                mock_result.run_id = "test-run-123"
                mock_result.task = None
                mock_result.code_artifacts = []
                mock_result.repo_changes = None
                mock_result.report_markdown = "## Success"
                mock_run.return_value = mock_result
                
                result = runner.invoke(app, [
                    "run",
                    "--file", data_path,
                    "--guide", guide_path,
                    "--task", "Parse bank statement",
                    "--dry-run"
                ])
                
                assert mock_run.called
                call_kwargs = mock_run.call_args[1] if mock_run.call_args[1] else {}
                spec_refs = call_kwargs.get('spec_refs', [])
                
                # Both file and guide should be in spec_refs
                assert len(spec_refs) == 2, f"Expected 2 refs, got {spec_refs}"
        finally:
            os.unlink(data_path)
            os.unlink(guide_path)
    
    def test_guide_with_csv_warns(self):
        """CLI warns when --guide used with CSV (self-describing, guide is irrelevant)."""
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as csv_file:
            csv_file.write(b"name,value\nfoo,1\n")
            csv_path = csv_file.name
        
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as guide_file:
            guide_file.write(b"%PDF-1.4\n")
            guide_path = guide_file.name
        
        try:
            with patch('integration_coworker.cli.design_and_generate_integration') as mock_run:
                mock_result = MagicMock()
                mock_result.run_id = "test-run-123"
                mock_result.task = None
                mock_result.code_artifacts = []
                mock_result.repo_changes = None
                mock_result.report_markdown = "## Success"
                mock_run.return_value = mock_result
                
                result = runner.invoke(app, [
                    "run",
                    "--file", csv_path,
                    "--guide", guide_path,
                    "--task", "Parse CSV with guide",
                    "--dry-run"
                ])
                
                # Should still succeed (warning, not error)
                assert mock_run.called
                # Warning should be emitted
                assert "Warning" in result.output or "self-describing" in result.output
        finally:
            os.unlink(csv_path)
            os.unlink(guide_path)


class TestCLIHelpText:
    """Tests for CLI help text includes file options."""
    
    def test_run_help_shows_file_option(self):
        """--help for run command shows --file option."""
        result = runner.invoke(app, ["run", "--help"])
        assert "--file" in result.output
        assert "CSV" in result.output or "data file" in result.output.lower()
    
    def test_run_help_shows_guide_option(self):
        """--help for run command shows --guide option."""
        result = runner.invoke(app, ["run", "--help"])
        assert "--guide" in result.output
        assert "PDF" in result.output or "fixed-width" in result.output.lower()
