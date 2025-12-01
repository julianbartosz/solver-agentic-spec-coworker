"""
Generated code quality tests.

Tests that generated code passes both Python's built-in compile checks
and external linters (ruff). This ensures the generated code is not just
syntactically valid but also follows best practices.

Run with: pytest tests/test_generated_code_quality.py -v
"""
import ast
import py_compile
import subprocess
import tempfile
import pytest
from pathlib import Path
from typing import List

from integration_coworker.api.entrypoint import design_and_generate_integration
from integration_coworker.api.types import IntegrationOptions
from integration_coworker.domain.models import CodeArtifact


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def generated_artifacts(tmp_path) -> List[CodeArtifact]:
    """Generate code artifacts for testing."""
    fixture_path = Path(__file__).parent / "fixtures" / "mock_payments_openapi.yaml"
    
    result = design_and_generate_integration(
        spec_refs=[str(fixture_path)],
        task_description="Create checkout session",
        provider_code="mock_payments",
        repo_root=str(tmp_path),
        options=IntegrationOptions(
            repo_integration_enabled=True,
            dry_run=False,
        ),
    )
    
    return result.code_artifacts


@pytest.fixture
def stripe_artifacts(tmp_path) -> List[CodeArtifact]:
    """Generate artifacts from the Stripe-like spec."""
    fixture_path = Path(__file__).parent / "fixtures" / "stripe_openapi_stub.yaml"
    
    if not fixture_path.exists():
        pytest.skip("Stripe fixture not available")
    
    result = design_and_generate_integration(
        spec_refs=[str(fixture_path)],
        task_description="Create payment intent",
        provider_code="stripe",
        repo_root=str(tmp_path),
        options=IntegrationOptions(
            repo_integration_enabled=True,
            dry_run=False,
        ),
    )
    
    return result.code_artifacts


# ==============================================================================
# py_compile validation tests
# ==============================================================================

class TestPyCompileValidation:
    """Tests using Python's built-in py_compile module."""
    
    def test_all_artifacts_pass_py_compile(self, generated_artifacts):
        """
        All generated artifacts should pass py_compile.compile().
        
        py_compile is stricter than ast.parse and catches issues like
        invalid encoding declarations and some bytecode generation problems.
        """
        for artifact in generated_artifacts:
            with tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.py',
                delete=False
            ) as f:
                f.write(artifact.content)
                temp_path = f.name
            
            try:
                # py_compile.compile will raise PyCompileError on failure
                py_compile.compile(temp_path, doraise=True)
            except py_compile.PyCompileError as e:
                pytest.fail(
                    f"Artifact {artifact.artifact_type} ({artifact.module_name}) "
                    f"failed py_compile:\n{e}"
                )
            finally:
                Path(temp_path).unlink(missing_ok=True)
    
    def test_client_artifact_compiles(self, generated_artifacts):
        """Client artifact specifically should compile."""
        client_artifacts = [a for a in generated_artifacts if a.artifact_type == "client"]
        assert len(client_artifacts) >= 1
        
        artifact = client_artifacts[0]
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.py',
            delete=False
        ) as f:
            f.write(artifact.content)
            temp_path = f.name
        
        try:
            py_compile.compile(temp_path, doraise=True)
        except py_compile.PyCompileError as e:
            pytest.fail(f"Client artifact failed compile: {e}")
        finally:
            Path(temp_path).unlink(missing_ok=True)
    
    def test_flow_artifact_compiles(self, generated_artifacts):
        """Flow artifact specifically should compile."""
        flow_artifacts = [a for a in generated_artifacts if a.artifact_type == "flow"]
        assert len(flow_artifacts) >= 1
        
        artifact = flow_artifacts[0]
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.py',
            delete=False
        ) as f:
            f.write(artifact.content)
            temp_path = f.name
        
        try:
            py_compile.compile(temp_path, doraise=True)
        except py_compile.PyCompileError as e:
            pytest.fail(f"Flow artifact failed compile: {e}")
        finally:
            Path(temp_path).unlink(missing_ok=True)


# ==============================================================================
# Ruff lint validation tests
# ==============================================================================

def is_ruff_available() -> bool:
    """Check if ruff is available on the system."""
    try:
        result = subprocess.run(
            ["ruff", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


@pytest.mark.skipif(not is_ruff_available(), reason="ruff not installed")
class TestRuffValidation:
    """Tests using ruff linter.
    
    Note: With mock LLM, we allow F401 (unused imports) and E501 (line too long)
    as the mock templates include standard imports that may not all be used and
    long module paths. Real LLM output should pass stricter checks.
    """
    
    # Rules to ignore for mock LLM output
    MOCK_LLM_IGNORED_RULES = "F401,E501"
    
    def _run_ruff(self, code: str, select: str = None, ignore: str = None) -> tuple[int, str]:
        """
        Run ruff on code and return (exit_code, output).
        
        Args:
            code: Python code to check
            select: Optional rule selectors (e.g., "E,F,I")
            ignore: Optional rules to ignore (e.g., "F401")
        
        Returns:
            Tuple of (exit_code, combined_output)
        """
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.py',
            delete=False
        ) as f:
            f.write(code)
            temp_path = f.name
        
        try:
            cmd = ["ruff", "check", temp_path, "--no-cache"]
            if select:
                cmd.extend(["--select", select])
            if ignore:
                cmd.extend(["--ignore", ignore])
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            return result.returncode, result.stdout + result.stderr
        finally:
            Path(temp_path).unlink(missing_ok=True)
    
    def test_artifacts_pass_critical_ruff_rules(self, generated_artifacts):
        """
        All artifacts should pass critical ruff rules (E/F).
        
        E = pycodestyle errors
        F = pyflakes errors (undefined names, unused imports, etc.)
        
        Note: F401 (unused imports) and E501 (line too long) are ignored for mock LLM output.
        """
        for artifact in generated_artifacts:
            # Ignore F401 (unused imports) and E501 (line too long) in mock output
            exit_code, output = self._run_ruff(
                artifact.content, 
                select="E,F",
                ignore=self.MOCK_LLM_IGNORED_RULES
            )
            
            if exit_code != 0:
                # Parse out which rules failed
                lines = output.strip().split('\n')
                errors = [l for l in lines if l and ':' in l]
                
                pytest.fail(
                    f"Artifact {artifact.artifact_type} ({artifact.module_name}) "
                    f"failed critical ruff rules:\n" + '\n'.join(errors[:10])
                )
    
    def test_client_artifact_passes_ruff(self, generated_artifacts):
        """Client artifact should pass ruff E/F rules."""
        client_artifacts = [a for a in generated_artifacts if a.artifact_type == "client"]
        assert len(client_artifacts) >= 1
        
        artifact = client_artifacts[0]
        exit_code, output = self._run_ruff(
            artifact.content, select="E,F", ignore=self.MOCK_LLM_IGNORED_RULES
        )
        
        if exit_code != 0:
            pytest.fail(f"Client failed ruff:\n{output[:500]}")
    
    def test_flow_artifact_passes_ruff(self, generated_artifacts):
        """Flow artifact should pass ruff E/F rules."""
        flow_artifacts = [a for a in generated_artifacts if a.artifact_type == "flow"]
        assert len(flow_artifacts) >= 1
        
        artifact = flow_artifacts[0]
        exit_code, output = self._run_ruff(
            artifact.content, select="E,F", ignore=self.MOCK_LLM_IGNORED_RULES
        )
        
        if exit_code != 0:
            pytest.fail(f"Flow failed ruff:\n{output[:500]}")
    
    def test_test_artifact_passes_ruff(self, generated_artifacts):
        """Test artifact should pass ruff E/F rules."""
        test_artifacts = [a for a in generated_artifacts if a.artifact_type == "test"]
        assert len(test_artifacts) >= 1
        
        artifact = test_artifacts[0]
        exit_code, output = self._run_ruff(
            artifact.content, select="E,F", ignore=self.MOCK_LLM_IGNORED_RULES
        )
        
        if exit_code != 0:
            pytest.fail(f"Test failed ruff:\n{output[:500]}")


# ==============================================================================
# Additional quality checks
# ==============================================================================

class TestCodeQualityMetrics:
    """Test basic quality metrics on generated code."""
    
    def test_no_bare_except(self, generated_artifacts):
        """Generated code should not use bare except clauses."""
        for artifact in generated_artifacts:
            tree = ast.parse(artifact.content)
            
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler):
                    if node.type is None:
                        # Allow if it re-raises
                        has_raise = any(
                            isinstance(child, ast.Raise) and child.exc is None
                            for child in ast.walk(node)
                        )
                        if not has_raise:
                            pytest.fail(
                                f"Artifact {artifact.artifact_type} uses bare except "
                                f"without re-raise at line {node.lineno}"
                            )
    
    def test_has_docstrings(self, generated_artifacts):
        """Generated code should have docstrings for classes and functions."""
        for artifact in generated_artifacts:
            tree = ast.parse(artifact.content)
            
            missing_docstrings = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    # Check for docstring
                    if not (
                        node.body 
                        and isinstance(node.body[0], ast.Expr) 
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)
                    ):
                        # Skip __init__ and simple property-like methods
                        if node.name not in ('__init__', '__repr__', '__str__'):
                            missing_docstrings.append(node.name)
            
            # Allow some missing docstrings but not all
            if len(missing_docstrings) > 3:
                pytest.fail(
                    f"Artifact {artifact.artifact_type} has many items missing docstrings: "
                    f"{missing_docstrings[:5]}..."
                )
    
    def test_reasonable_line_length(self, generated_artifacts):
        """Generated code should not have excessively long lines."""
        max_line_length = 120  # Generous limit
        
        for artifact in generated_artifacts:
            lines = artifact.content.split('\n')
            long_lines = [
                (i + 1, len(line))
                for i, line in enumerate(lines)
                if len(line) > max_line_length
            ]
            
            if len(long_lines) > 5:  # Allow a few long lines
                pytest.fail(
                    f"Artifact {artifact.artifact_type} has too many long lines (>{max_line_length} chars): "
                    f"lines {[ln for ln, _ in long_lines[:5]]}"
                )
    
    def test_no_hardcoded_secrets_patterns(self, generated_artifacts):
        """Generated code should not have hardcoded secret patterns."""
        # Look for actual hardcoded secrets (strings that look like real API keys)
        secret_patterns = [
            ("sk_live_", "Stripe live key"),
            ("sk_test_", "Stripe test key"),
            ("AKIA", "AWS access key"),
        ]
        
        for artifact in generated_artifacts:
            for pattern, description in secret_patterns:
                if pattern in artifact.content:
                    # Check if it's a real key (long alphanumeric string following the pattern)
                    import re
                    real_key_pattern = re.compile(rf'{re.escape(pattern)}[a-zA-Z0-9]{{20,}}')
                    matches = real_key_pattern.findall(artifact.content)
                    
                    if matches:
                        pytest.fail(
                            f"Artifact {artifact.artifact_type} may have hardcoded {description}: "
                            f"{matches[0][:20]}..."
                        )


class TestCodeStructure:
    """Test structural quality of generated code."""
    
    def test_imports_at_top(self, generated_artifacts):
        """Imports should be at the top of the file (after docstring)."""
        for artifact in generated_artifacts:
            tree = ast.parse(artifact.content)
            
            # Find first import and first non-import statement
            first_import_lineno = None
            first_other_lineno = None
            
            for node in ast.iter_child_nodes(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    if first_import_lineno is None:
                        first_import_lineno = node.lineno
                elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                    # This is the module docstring, skip it
                    pass
                else:
                    if first_other_lineno is None:
                        first_other_lineno = node.lineno
                    # Check if there are imports after this
                    if first_import_lineno and node.lineno < first_import_lineno:
                        pytest.fail(
                            f"Artifact {artifact.artifact_type} has import at line {first_import_lineno} "
                            f"after other code at line {node.lineno}"
                        )
    
    def test_no_unused_imports(self, generated_artifacts):
        """
        Generated code should not have obviously unused imports.
        
        Note: This is a basic check; ruff does this more thoroughly.
        """
        for artifact in generated_artifacts:
            tree = ast.parse(artifact.content)
            
            # Collect imported names
            imported_names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imported_names.add(alias.asname or alias.name.split('.')[0])
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        imported_names.add(alias.asname or alias.name)
            
            # Collect used names
            used_names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    used_names.add(node.id)
                elif isinstance(node, ast.Attribute):
                    # For x.y.z, we care about x
                    if isinstance(node.value, ast.Name):
                        used_names.add(node.value.id)
            
            # Check for unused
            unused = imported_names - used_names
            
            # Filter out common "used implicitly" imports
            ignored = {'TYPE_CHECKING', 'Optional', 'List', 'Dict', 'Any', 'Union', 'Callable'}
            unused = unused - ignored
            
            if len(unused) > 3:  # Allow a few unused imports
                pytest.fail(
                    f"Artifact {artifact.artifact_type} has potentially unused imports: {sorted(unused)[:5]}"
                )
