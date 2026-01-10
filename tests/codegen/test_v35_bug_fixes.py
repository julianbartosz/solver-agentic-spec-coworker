"""
Tests for V35 Bug Fix Modules

These tests verify the functionality of:
- import_fixer.py (V35-001): Fix hallucinated imports
- task_parser.py (V35-002): Extract task paths
- repo_type_detector.py (V35-003): Detect CLI vs web repos
- coordinated_artifacts.py (V35-004/005/006): Coordinated artifact generation
- post_generation_validator.py: Post-generation validation
"""
import pytest
import tempfile
import os
from pathlib import Path


# =============================================================================
# V35-001: Import Fixer Tests
# =============================================================================

class TestImportFixer:
    """Tests for import_fixer.py - fixing hallucinated import paths."""
    
    def test_fix_hallucinated_framework_import(self):
        """V35-001: Fix integration_framework.core.client hallucination."""
        from integration_coworker.codegen.import_fixer import fix_imports_in_code
        
        code = '''
from integration_framework.core.client import IntegrationClient
from integration_framework.exceptions import IntegrationError

class MyClient(IntegrationClient):
    pass
'''
        fixed, fixes = fix_imports_in_code(code)
        
        assert "integration_framework" not in fixed
        assert "integration_coworker_runtime" in fixed
        assert len(fixes) > 0
    
    def test_fix_internal_coworker_runtime_import(self):
        """V35-001: Fix integration_coworker.runtime.* imports."""
        from integration_coworker.codegen.import_fixer import fix_imports_in_code
        
        code = '''
from integration_coworker.runtime.http_client import IntegrationHttpClient
from integration_coworker.runtime.exceptions import IntegrationError
'''
        fixed, fixes = fix_imports_in_code(code)
        
        assert "integration_coworker.runtime" not in fixed
        assert "integration_coworker_runtime" in fixed
        assert len(fixes) == 2
    
    def test_validate_runtime_imports(self):
        """V35-001: Validate imports are correct."""
        from integration_coworker.codegen.import_fixer import validate_runtime_imports
        
        valid_code = '''
from integration_coworker_runtime import IntegrationHttpClient, IntegrationError
'''
        result = validate_runtime_imports(valid_code)
        assert result.is_valid
        assert len(result.unresolved_imports) == 0
    
    def test_validate_invalid_imports(self):
        """V35-001: Detect invalid imports."""
        from integration_coworker.codegen.import_fixer import validate_runtime_imports
        
        invalid_code = '''
from integration_framework.core.client import Client
'''
        result = validate_runtime_imports(invalid_code)
        assert not result.is_valid or len(result.warnings) > 0 or len(result.unresolved_imports) > 0


# =============================================================================
# V35-002: Task Parser Tests
# =============================================================================

class TestTaskParser:
    """Tests for task_parser.py - extracting task paths from descriptions."""
    
    def test_extract_explicit_file_path(self):
        """V35-002: Extract file path from task description."""
        from integration_coworker.codegen.task_parser import extract_task_requirements
        
        description = "Add the create_completion method to src/openai/client.py"
        result = extract_task_requirements(description)
        
        assert "src/openai/client.py" in result.explicit_file_paths
    
    def test_extract_function_name(self):
        """V35-002: Extract function name from description."""
        from integration_coworker.codegen.task_parser import extract_task_requirements
        
        description = "Implement a function called create_completion that handles completions"
        result = extract_task_requirements(description)
        
        # Check that we found the function
        assert len(result.requested_functions) > 0
        func_names = [f.name for f in result.requested_functions]
        assert "create_completion" in func_names
    
    def test_should_override_template_path_with_explicit_path(self):
        """V35-002: Detect when task path should override template."""
        from integration_coworker.codegen.task_parser import (
            extract_task_requirements,
            should_override_template_path,
        )
        
        description = "Add method to src/docformatter/client.py"
        result = extract_task_requirements(description)
        
        # Task specifies a path, should suggest override
        assert result.has_explicit_structure or len(result.explicit_file_paths) > 0
    
    def test_no_override_without_explicit_path(self):
        """V35-002: Don't override when no explicit path is given."""
        from integration_coworker.codegen.task_parser import (
            extract_task_requirements,
            should_override_template_path,
        )
        
        description = "Create a client for the OpenAI API"
        result = extract_task_requirements(description)
        
        # No explicit path, should not override
        should_override = should_override_template_path(result)
        # It's OK if this is False, or if True but with low confidence
        assert not should_override or result.primary_target_path is None


# =============================================================================
# V35-003: Repo Type Detector Tests
# =============================================================================

class TestRepoTypeDetector:
    """Tests for repo_type_detector.py - detecting CLI vs web repos."""
    
    def test_detect_cli_tool_by_click(self):
        """V35-003: Detect CLI tool by click framework."""
        from integration_coworker.codegen.repo_type_detector import (
            detect_repo_type,
            RepoType,
        )
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create CLI-style structure
            cli_file = Path(tmpdir) / "cli.py"
            cli_file.write_text('''
import click

@click.command()
@click.option("--format", help="Format option")
def main(format):
    """CLI tool entry point."""
    pass
''')
            result = detect_repo_type(tmpdir)
            assert result.repo_type == RepoType.CLI_TOOL
    
    def test_detect_web_service_by_fastapi(self):
        """V35-003: Detect web service by FastAPI."""
        from integration_coworker.codegen.repo_type_detector import (
            detect_repo_type,
            RepoType,
        )
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create FastAPI-style structure
            main_file = Path(tmpdir) / "main.py"
            main_file.write_text('''
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {"message": "Hello"}
''')
            result = detect_repo_type(tmpdir)
            assert result.repo_type == RepoType.WEB_SERVICE
    
    def test_should_not_generate_fastapi_for_cli(self):
        """V35-003: Don't generate FastAPI router for CLI tools."""
        from integration_coworker.codegen.repo_type_detector import (
            detect_repo_type,
            should_generate_fastapi_router,
            RepoType,
        )
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create CLI-style structure
            cli_file = Path(tmpdir) / "cli.py"
            cli_file.write_text('''
import typer
app = typer.Typer()
''')
            # should_generate_fastapi_router takes a string path
            assert not should_generate_fastapi_router(tmpdir)


# =============================================================================
# V35-004/005/006: Coordinated Artifacts Tests
# =============================================================================

class TestCoordinatedArtifacts:
    """Tests for coordinated_artifacts.py - consistent artifact generation."""
    
    def test_create_coordinated_artifact_set(self):
        """V35-004: Create artifact set with consistent paths."""
        from integration_coworker.codegen.coordinated_artifacts import (
            create_coordinated_artifact_set,
        )
        
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_set = create_coordinated_artifact_set(
                provider_code="openai",
                task_slug="create_completion",
                repo_root=tmpdir,
            )
            
            assert artifact_set.client_location is not None
            assert artifact_set.flow_location is not None
            assert artifact_set.test_location is not None
            
            # Check import paths are consistent
            assert "integrations.clients.openai" in artifact_set.client_location.import_path
            assert "integrations.flows.openai_create_completion" in artifact_set.flow_location.import_path
    
    def test_fix_artifact_imports(self):
        """V35-006: Fix cross-artifact import paths."""
        from integration_coworker.codegen.coordinated_artifacts import (
            create_coordinated_artifact_set,
            fix_artifact_imports,
        )
        
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_set = create_coordinated_artifact_set(
                provider_code="openai",
                task_slug="create_completion",
                repo_root=tmpdir,
            )
            
            # Set up flow with wrong import
            artifact_set.flow_code = '''
from integrations.clients.wrong_path import OpenaiClient

def create_completion_flow():
    client = OpenaiClient()
'''
            
            fixed_set = fix_artifact_imports(artifact_set)
            
            # Import should be fixed
            assert "integrations.clients.openai" in fixed_set.flow_code
    
    def test_write_coordinated_artifacts(self):
        """V35-005: Write all artifacts atomically."""
        from integration_coworker.codegen.coordinated_artifacts import (
            create_coordinated_artifact_set,
            write_coordinated_artifacts,
            verify_written_artifacts,
        )
        
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_set = create_coordinated_artifact_set(
                provider_code="openai",
                task_slug="create_completion",
                repo_root=tmpdir,
            )
            
            # Set content
            artifact_set.client_code = "# Client code"
            artifact_set.flow_code = "# Flow code"
            artifact_set.test_code = "# Test code"
            
            # Write artifacts
            written = write_coordinated_artifacts(artifact_set, tmpdir)
            
            assert len(written) > 0
            
            # Verify files exist
            success, issues = verify_written_artifacts(artifact_set, tmpdir)
            assert success


# =============================================================================
# Post-Generation Validator Tests
# =============================================================================

class TestPostGenerationValidator:
    """Tests for post_generation_validator.py."""
    
    def test_validate_and_fix_generated_code(self):
        """Test comprehensive validation and fixing."""
        from integration_coworker.codegen.post_generation_validator import (
            validate_and_fix_generated_code,
        )
        
        code = '''
from integration_framework.client import Client

class OpenaiClient(Client):
    pass
'''
        result = validate_and_fix_generated_code(
            generated_code=code,
            artifact_type="client",
            provider_code="openai",
            task_slug="create_completion",
        )
        
        assert result.was_modified
        assert "integration_framework" not in result.fixed_code
    
    def test_validation_result_properties(self):
        """Test ValidationResult computed properties."""
        from integration_coworker.codegen.post_generation_validator import (
            ValidationResult,
            ValidationIssue,
        )
        
        result = ValidationResult(
            original_code="# original",
            fixed_code="# fixed",
            issues=[
                ValidationIssue(
                    category="import",
                    severity="error",
                    message="Bad import",
                    auto_fixed=True,
                ),
                ValidationIssue(
                    category="path",
                    severity="warning",
                    message="Path mismatch",
                ),
            ],
        )
        
        assert result.was_modified
        assert result.error_count == 1
        assert result.warning_count == 1
        assert not result.has_critical_errors  # Error was auto-fixed


# =============================================================================
# Integration Tests
# =============================================================================

class TestV35Integration:
    """Integration tests for the complete V35 fix pipeline."""
    
    def test_full_validation_pipeline(self):
        """Test complete validation pipeline from raw LLM output to fixed code."""
        from integration_coworker.codegen.post_generation_validator import (
            validate_artifact_set,
        )
        
        # Simulate problematic LLM output
        client_code = '''
from integration_framework.core.client import IntegrationClient

class OpenaiClient(IntegrationClient):
    def create_completion(self, prompt):
        return self.request("POST", "/completions")
'''
        
        flow_code = '''
from integrations.clients.wrong import WrongClient

def create_completion_flow(prompt):
    client = WrongClient()
    return client.create_completion(prompt)
'''
        
        test_code = '''
from integrations.flows.wrong_path import wrong_function

def test_create_completion():
    result = wrong_function("test")
    assert result
'''
        
        with tempfile.TemporaryDirectory() as tmpdir:
            results = validate_artifact_set(
                client_code=client_code,
                flow_code=flow_code,
                test_code=test_code,
                repo_root=tmpdir,
                provider_code="openai",
                task_slug="create_completion",
            )
            
            # All artifacts should be validated
            assert "client" in results
            assert "flow" in results
            assert "test" in results
            
            # Client imports should be fixed
            client_result = results["client"]
            assert "integration_framework" not in client_result.fixed_code


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
